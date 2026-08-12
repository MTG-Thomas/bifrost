"""Transactional orchestration for loose files beneath the global ``_repo`` root.

Object storage cannot participate in the PostgreSQL transaction. Activation therefore
serializes writers, performs a path-level CAS, and compensates S3 changes on failure.
No staged content is visible before activation.
"""

from __future__ import annotations

import ast
import base64
import difflib
import hashlib
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.workspace_repo_changesets import (
    ChangesetStatus,
    WorkspaceRepoActivateRequest,
    WorkspaceRepoChangesetBegin,
    WorkspaceRepoChangesetDiffResponse,
    WorkspaceRepoChangesetResponse,
    WorkspaceRepoFileDiff,
    WorkspaceRepoFileMutationRequest,
    WorkspaceRepoMutation,
    WorkspaceRepoStateResponse,
    WorkspaceRepoValidationResponse,
)
from src.models.orm.workspace_repo_changesets import WorkspaceRepoChangeset
from src.repositories.workspace_repo_changesets import (
    RETRYABLE_GIT_FAILURE_STATES,
    WorkspaceRepoChangesetRepository,
)
from src.services.file_storage import FileStorageService
from src.services.file_storage.ast_parser import ASTMetadataParser
from src.services.file_storage.deactivation import DeactivationProtectionService
from src.services.platform_commit_writer import (
    PlatformCommitError,
    PlatformCommitFile,
    PlatformCommitRequest,
    PlatformCommitWriter,
)
from src.services.repo_storage import RepoStorage

logger = logging.getLogger(__name__)


class ChangesetConflict(Exception):
    def __init__(self, reason: str, **detail):
        self.detail = {"reason": reason, **detail}
        super().__init__(reason)


class ChangesetInvalid(Exception):
    pass


class OrganizationScopeRequired(Exception):
    pass


def require_organization_id(organization_id: UUID | None) -> UUID:
    if organization_id is None:
        raise OrganizationScopeRequired(
            "workspace _repo changesets require an organization-scoped platform administrator"
        )
    return organization_id


@dataclass(frozen=True)
class WorkspaceGitClosureResult:
    """Structured result for the legacy generated-checkout Git helper.

    Transactional changesets use ``PlatformCommitWriter`` directly.  The
    legacy helper retains this type so its checkout, push, and convergence
    observations cannot be confused with authoritative closure success.
    """

    commit_sha: str | None
    push_error: str | None
    remote_sha: str | None = None
    authoritative_revision: str | None = None
    authoritative_files: dict[str, str] | None = None
    mismatch_paths: list[str] | None = None

    def __iter__(self):
        yield self.commit_sha
        yield self.push_error


class WorkspaceRepoChangesetService:
    ACTIVE = {"open", "staged", "validated"}
    ABORTABLE = ACTIVE | {"activating", "conflicted"}
    RETRYABLE_GIT_FAILURES = {
        ("activated", "git_closure"),
        ("committed_unpushed", "git_push"),
        ("committed_unpushed", "remote_verification"),
    }

    def __init__(
        self,
        db: AsyncSession,
        organization_id: UUID,
        repo: RepoStorage | None = None,
        commit_writer: PlatformCommitWriter | None = None,
    ):
        self.db = db
        self.organization_id = organization_id
        self.repo = repo or RepoStorage()
        self.rows = WorkspaceRepoChangesetRepository(db)
        self.commit_writer = commit_writer

    @staticmethod
    def normalize_scope(scope: str) -> str:
        value = scope.replace("\\", "/").strip("/")
        if not value or value in {".", ".."} or ".." in PurePosixPath(value).parts:
            raise ChangesetInvalid(
                "scope must be a non-empty workspace-relative prefix"
            )
        return value

    @classmethod
    def normalize_path(cls, path: str, scope: str) -> str:
        value = path.replace("\\", "/").strip("/")
        if not value or ".." in PurePosixPath(value).parts:
            raise ChangesetInvalid(
                "path must be workspace-relative and cannot contain '..'"
            )
        if value != scope and not value.startswith(f"{scope}/"):
            raise ChangesetInvalid(
                f"path {value!r} is outside changeset scope {scope!r}"
            )
        return value

    async def _snapshot(self, scope: str) -> tuple[str, dict[str, str]]:
        paths = sorted(await self.repo.list(f"{scope}/"))
        if await self.repo.exists(scope):
            paths.insert(0, scope)
        files: dict[str, str] = {}
        digest = hashlib.sha256()
        for path in paths:
            content = await self.repo.read(path)
            content_hash = hashlib.sha256(content).hexdigest()
            files[path] = content_hash
            digest.update(path.encode())
            digest.update(b"\0")
            digest.update(content_hash.encode())
            digest.update(b"\n")
        return digest.hexdigest(), files

    async def state(
        self, scope: str, git_status: dict | None = None, workspace_dirty: bool = False
    ) -> WorkspaceRepoStateResponse:
        scope = self.normalize_scope(scope)
        revision, files = await self._snapshot(scope)
        count = await self.rows.count_open(scope, self.organization_id)
        return WorkspaceRepoStateResponse(
            scope=scope,
            revision=revision,
            file_count=len(files),
            file_hashes=files,
            dirty=workspace_dirty or count > 0,
            open_changesets=count,
            git_status=git_status,
        )

    async def begin(
        self, request: WorkspaceRepoChangesetBegin, user_id: UUID
    ) -> WorkspaceRepoChangesetResponse:
        scope = self.normalize_scope(request.scope)
        revision, files = await self._snapshot(scope)
        if request.base_revision is not None and request.base_revision != revision:
            raise ChangesetConflict(
                "revision_mismatch",
                base_revision=request.base_revision,
                current_revision=revision,
                conflicting_paths=[],
            )
        row = WorkspaceRepoChangeset(
            organization_id=self.organization_id,
            scope=scope,
            base_revision=revision,
            base_files=files,
            mutations=[],
            status="open",
            title=request.title,
            worker_id=request.worker_id,
            created_by=user_id,
        )
        await self.rows.add(row)
        return self._response(row)

    async def get(self, changeset_id: UUID) -> WorkspaceRepoChangesetResponse:
        return self._response(await self._required(changeset_id))

    async def prepare_writer_enqueue(
        self,
        changeset_id: UUID,
        request: WorkspaceRepoActivateRequest,
        operation: Literal["activate", "retry"],
    ) -> WorkspaceRepoChangesetResponse:
        """Lock and validate one changeset through enqueue and job assignment."""
        row = await self._required(changeset_id, for_update=True)
        if operation == "activate" and row.status not in {"validated", "activating"}:
            raise ChangesetInvalid(
                "changeset must pass validation immediately before activation"
            )
        if operation == "retry":
            self._ensure_retryable_git_closure(row, request)
        return self._response(row)

    async def assign_writer_job(self, changeset_id: UUID, job_id: UUID) -> None:
        row = await self._required(changeset_id, for_update=True)
        row.writer_job_id = job_id
        await self.db.flush()

    async def list_active(self) -> list[WorkspaceRepoChangesetResponse]:
        rows = await self.rows.list_by_statuses(
            self.organization_id,
            ("open", "staged", "validated", "activating"),
        )
        return [self._response(row) for row in rows]

    async def closure_ledger(self) -> list[WorkspaceRepoChangesetResponse]:
        rows = await self.rows.list_by_statuses(
            self.organization_id,
            ("activated", "committed_unpushed", "committed", "recovery_required"),
        )
        return [self._response(row) for row in rows]

    async def stage(
        self, changeset_id: UUID, request: WorkspaceRepoFileMutationRequest
    ) -> WorkspaceRepoChangesetResponse:
        row = await self._required(changeset_id, for_update=True)
        self._ensure_active(row)
        path = self.normalize_path(request.path, row.scope)
        before_hash = row.base_files.get(path)
        if request.expected_hash is not None and request.expected_hash != before_hash:
            raise ChangesetConflict(
                "file_revision_mismatch",
                path=path,
                expected_hash=request.expected_hash,
                current_hash=before_hash,
            )
        content = None
        after_hash = None
        if request.operation == "write":
            try:
                raw = base64.b64decode(request.content_base64 or "", validate=True)
            except Exception as exc:
                raise ChangesetInvalid("content_base64 is not valid base64") from exc
            content = request.content_base64
            after_hash = hashlib.sha256(raw).hexdigest()
        mutation = {
            "path": path,
            "operation": request.operation,
            "content_base64": content,
            "before_hash": before_hash,
            "after_hash": after_hash,
            "force_deactivation": request.force_deactivation,
        }
        mutations = [item for item in row.mutations if item["path"] != path]
        mutations.append(mutation)
        row.mutations = mutations
        row.status = "staged"
        row.validation = None
        await self.db.flush()
        return self._response(row)

    async def diff(self, changeset_id: UUID) -> WorkspaceRepoChangesetDiffResponse:
        row = await self._required(changeset_id)
        result = []
        for item in row.mutations:
            before = await self._read_optional(item["path"])
            current_hash = (
                hashlib.sha256(before).hexdigest() if before is not None else None
            )
            if current_hash != item.get("before_hash"):
                raise ChangesetConflict(
                    "file_revision_mismatch",
                    path=item["path"],
                    expected_hash=item.get("before_hash"),
                    current_hash=current_hash,
                )
            after = (
                base64.b64decode(item["content_base64"])
                if item["operation"] == "write"
                else None
            )
            unified = self._unified_diff(item["path"], before, after)
            result.append(
                WorkspaceRepoFileDiff(
                    path=item["path"],
                    operation=item["operation"],
                    before_hash=item.get("before_hash"),
                    after_hash=item.get("after_hash"),
                    unified_diff=unified,
                )
            )
        return WorkspaceRepoChangesetDiffResponse(changeset_id=row.id, files=result)

    async def validate(self, changeset_id: UUID) -> WorkspaceRepoValidationResponse:
        row = await self._required(changeset_id, for_update=True)
        self._ensure_active(row)
        diagnostics: list[dict] = []
        pending: list[dict] = []
        deactivation = DeactivationProtectionService(self.db)
        parser = ASTMetadataParser()
        for item in row.mutations:
            path = item["path"]
            if not path.endswith(".py"):
                continue
            names: set[str] = set()
            decorator_info: dict[str, tuple[str, str]] = {}
            if item["operation"] == "write":
                raw = base64.b64decode(item["content_base64"])
                try:
                    tree = ast.parse(raw.decode("utf-8"), filename=path)
                except (SyntaxError, UnicodeDecodeError) as exc:
                    diagnostics.append(
                        {
                            "path": path,
                            "severity": "error",
                            "source": "syntax",
                            "message": str(exc),
                            "line": getattr(exc, "lineno", None),
                            "column": getattr(exc, "offset", None),
                        }
                    )
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    for decorator in node.decorator_list:
                        parsed = parser.parse_decorator(decorator)
                        if parsed and parsed[0] in {
                            "workflow",
                            "tool",
                            "data_provider",
                        }:
                            kind, kwargs = parsed
                            names.add(node.name)
                            decorator_info[node.name] = (
                                kind,
                                kwargs.get("name") or node.name,
                            )
            if not item.get("force_deactivation"):
                found, _ = await deactivation.detect_pending_deactivations(
                    path=path,
                    new_function_names=names,
                    new_decorator_info=decorator_info,
                )
                pending.extend({**asdict(value), "path": path} for value in found)
        valid = not diagnostics and not pending and bool(row.mutations)
        current_revision, _ = await self._snapshot(row.scope)
        result = WorkspaceRepoValidationResponse(
            valid=valid,
            diagnostics=diagnostics,
            pending_deactivations=pending,
            validated_revision=current_revision,
        )
        row.validation = result.model_dump(mode="json")
        row.status = "validated" if valid else "staged"
        await self.db.flush()
        return result

    async def activate(
        self,
        changeset_id: UUID,
        request: WorkspaceRepoActivateRequest,
        updated_by: str,
        *,
        writer_job_id: UUID | None = None,
    ) -> WorkspaceRepoChangesetResponse:
        if request.commit_message and not request.push:
            raise ChangesetInvalid("verified platform Git closure requires push=true")
        # The durable platform-job resource lease owns the authoritative writer
        # across this whole operation. This advisory lock only serializes the
        # short state transition that makes the changeset immutable before any
        # object-store reads; it is deliberately released before network I/O.
        await self.db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext('bifrost:workspace-repo-changesets'))"
            )
        )
        row = await self._required(changeset_id, for_update=True)
        resuming = row.status == "activating"
        if not resuming and (
            row.status != "validated"
            or not row.validation
            or not row.validation.get("valid")
        ):
            raise ChangesetInvalid(
                "changeset must pass validation immediately before activation"
            )
        mutations = [dict(item) for item in row.mutations]
        scope = row.scope
        base_revision = row.base_revision
        existing_backup = dict(row.activation_backup or {})
        had_existing_backup = bool(existing_backup)
        existing_failure = (
            dict(row.failure_detail) if isinstance(row.failure_detail, dict) else {}
        )
        if resuming and not existing_backup and existing_failure.get("phase") not in {
            "activation_snapshot",
            None,
        }:
            raise ChangesetInvalid(
                "activating changeset has no durable source backup"
            )
        row.status = "activating"
        if writer_job_id is not None:
            row.writer_job_id = writer_job_id
        row.commit_message = request.commit_message
        row.push_requested = request.push
        row.closure_started_at = row.closure_started_at or datetime.now(timezone.utc)
        row.failure_detail = {
            "phase": "activation_snapshot",
            "state": "pending",
            "writer_job_id": str(writer_job_id) if writer_job_id else None,
        }
        await self.db.flush()
        # Once this commits, staging is closed and the durable writer lease
        # prevents every other authoritative writer while snapshots are read.
        await self.db.commit()

        current_revision, current_files = await self._snapshot(scope)
        if existing_backup:
            conflicting = [
                item["path"]
                for item in mutations
                if current_files.get(item["path"])
                not in {item.get("before_hash"), item.get("after_hash")}
            ]
        else:
            conflicting = [
                item["path"]
                for item in mutations
                if current_files.get(item["path"]) != item.get("before_hash")
            ]
        if conflicting:
            row = await self._required(changeset_id, for_update=True)
            row.status = "conflicted"
            row.failure_detail = {
                "phase": "activation",
                "state": "conflicted",
                "conflicting_paths": conflicting,
            }
            await self.db.flush()
            await self.db.commit()
            raise ChangesetConflict(
                "revision_mismatch",
                base_revision=base_revision,
                current_revision=current_revision,
                conflicting_paths=conflicting,
            )

        if existing_backup:
            originals = {
                path: (base64.b64decode(content) if content is not None else None)
                for path, content in existing_backup.items()
            }
        else:
            originals = {
                item["path"]: await self._read_optional(item["path"])
                for item in mutations
            }
            existing_backup = {
                path: (base64.b64encode(content).decode() if content is not None else None)
                for path, content in originals.items()
            }

        from src.services.workspace_convergence import snapshot_repo_storage

        if had_existing_backup:
            authoritative_base_files = row.authoritative_base_files
            if authoritative_base_files is None:
                raise ChangesetInvalid(
                    "activating changeset has no durable authoritative base snapshot"
                )
        else:
            authoritative_base_files = (
                await snapshot_repo_storage(self.repo)
            ).file_hashes

        row = await self._required(changeset_id, for_update=True)
        if row.status != "activating":
            raise ChangesetConflict("changeset_state_changed", status=row.status)
        row.activation_backup = existing_backup
        row.authoritative_base_files = authoritative_base_files
        row.failure_detail = {
            "phase": "activation",
            "state": "pending",
            "writer_job_id": str(writer_job_id) if writer_job_id else None,
        }
        await self.db.flush()
        # Persist the exact recovery image before the first authoritative write.
        await self.db.commit()

        storage = FileStorageService(self.db)
        from src.core.workspace_writer import (
            WorkspaceWriterBusy,
            WorkspaceWriterLeaseLost,
            checkpoint_workspace_writer_lease,
        )

        python_paths = [
            item["path"] for item in mutations if item["path"].endswith(".py")
        ]
        from src.core.module_cache import workspace_source_update

        async with workspace_source_update(
            reason="workspace_changeset_activated",
            changed_paths=python_paths,
            broadcast=True,
        ):
            try:
                for item in mutations:
                    if item["operation"] == "delete":
                        await storage.delete_file(item["path"])
                    else:
                        result = await storage.write_file(
                            item["path"],
                            base64.b64decode(item["content_base64"]),
                            updated_by=updated_by,
                            force_deactivation=bool(item.get("force_deactivation")),
                        )
                        if result.pending_deactivations:
                            raise ChangesetInvalid(
                                f"deactivation preflight changed for {item['path']}"
                            )
                    # FileStorage combines one object-store mutation with its
                    # metadata updates. Commit each completed file so the
                    # durable lease, not a long DB transaction, serializes the
                    # multi-file activation.
                    await self.db.commit()
                await checkpoint_workspace_writer_lease(self.db)
                activated_revision, _ = await self._snapshot(scope)
                from src.core.repo_dirty import get_repo_dirty_state
                from src.services.workspace_convergence import snapshot_repo_storage

                dirty_state = await get_repo_dirty_state()
                if dirty_state is None or dirty_state.generation is None:
                    raise RuntimeError(
                        "activation completed without a generation-fenced dirty marker"
                    )
                authoritative = await snapshot_repo_storage(self.repo)
                await checkpoint_workspace_writer_lease(self.db)
                row = await self._required(changeset_id, for_update=True)
                row.activated_revision = activated_revision
                row.dirty_generation = dirty_state.generation
                row.authoritative_revision = authoritative.revision
                row.authoritative_files = authoritative.file_hashes
                row.activation_backup = None
                row.status = "activated"
                row.failure_detail = None
                await self.db.flush()
                # Activation is the authoritative workspace transition. Make it
                # durable before crossing the independent Git boundary.
                await self.db.commit()
            except (WorkspaceWriterBusy, WorkspaceWriterLeaseLost):
                # A stale runner must not compensate or rewrite the durable
                # record after ownership moved. The activating row and exact
                # backup intentionally remain available for a fenced retry or
                # CAS-checked abort.
                await self.db.rollback()
                raise
            except Exception as exc:
                await self.db.rollback()
                # Compensate through the normal facade so S3, index, workflow metadata,
                # module cache, app previews, and activity observers converge on the
                # restored state rather than leaving a direct-storage-only rollback.
                rollback_errors: list[str] = []
                for path, content in originals.items():
                    try:
                        if content is None:
                            await storage.delete_file(path)
                        else:
                            restored = await storage.write_file(
                                path,
                                content,
                                updated_by="changeset-rollback",
                                force_deactivation=True,
                            )
                            if restored.pending_deactivations:
                                raise RuntimeError(
                                    f"deactivation preflight blocked restore of {path}"
                                )
                        await self.db.commit()
                    except Exception as rollback_exc:
                        await self.db.rollback()
                        rollback_errors.append(f"{path}: {rollback_exc}")
                row = await self._required(changeset_id, for_update=True)
                row.status = "recovery_required" if rollback_errors else "failed"
                row.error = str(exc)
                row.failure_detail = {
                    "phase": "activation",
                    "message": str(exc),
                    "rollback": {
                        "state": "failed" if rollback_errors else "completed",
                        "errors": rollback_errors,
                    },
                }
                if rollback_errors:
                    row.error = (
                        f"{row.error}; rollback failed: {'; '.join(rollback_errors)}"
                    )
                await self.db.flush()
                # Failure state and the compensating writes are part of the durable
                # audit record even though the HTTP request returns an error.
                await self.db.commit()
                if rollback_errors:
                    raise RuntimeError(row.error) from exc
                raise

        return await self._complete_git_closure(
            changeset_id, request, operator=updated_by
        )

    async def retry_git_closure(
        self,
        changeset_id: UUID,
        request: WorkspaceRepoActivateRequest,
        operator: str,
        *,
        writer_job_id: UUID | None = None,
    ) -> WorkspaceRepoChangesetResponse:
        """Retry only failed Git closure after workspace activation succeeded."""
        await self.db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext('bifrost:workspace-repo-changesets'))"
            )
        )
        row = await self._required(changeset_id, for_update=True)
        self._ensure_retryable_git_closure(row, request)
        if writer_job_id is not None:
            row.writer_job_id = writer_job_id
        return await self._complete_git_closure(
            changeset_id, request, operator=operator
        )

    async def check_retryable_git_closure(
        self, changeset_id: UUID, request: WorkspaceRepoActivateRequest
    ) -> None:
        row = await self._required(changeset_id)
        self._ensure_retryable_git_closure(row, request)

    def _ensure_retryable_git_closure(
        self,
        row: WorkspaceRepoChangeset,
        request: WorkspaceRepoActivateRequest,
    ) -> None:
        failure = row.failure_detail if isinstance(row.failure_detail, dict) else {}
        retry_key = (row.status, str(failure.get("phase") or ""))
        if (
            retry_key not in self.RETRYABLE_GIT_FAILURES
            or failure.get("state") not in RETRYABLE_GIT_FAILURE_STATES
        ):
            raise ChangesetInvalid(
                f"changeset in {row.status!r} state does not have a retryable Git closure failure"
            )
        prior_provenance = (
            failure.get("provenance")
            if isinstance(failure.get("provenance"), dict)
            else {}
        )
        original_message = prior_provenance.get("commit_message")
        if not original_message and not request.commit_message:
            raise ChangesetInvalid(
                "retrying legacy Git closure requires the original commit_message"
            )
        if (
            original_message
            and request.commit_message
            and request.commit_message != original_message
        ):
            raise ChangesetInvalid(
                "retry commit_message must match the original Git closure provenance"
            )
        if not request.push:
            raise ChangesetInvalid("verified platform Git closure requires push=true")
        if (
            row.commit_message
            and request.commit_message
            and row.commit_message != request.commit_message
        ):
            raise ChangesetInvalid(
                "retry commit_message must match the recorded closure message"
            )

    async def recoverable_git_closures(
        self, *, scope: str | None = None
    ) -> list[WorkspaceRepoChangesetResponse]:
        """List this organization's explicit failed Git closure records."""
        rows = await self.rows.list_retryable_git_failures(
            self.organization_id, scope=scope
        )
        return [self._response(row) for row in rows]

    async def _complete_git_closure(
        self,
        changeset_id: UUID,
        request: WorkspaceRepoActivateRequest,
        *,
        operator: str,
    ) -> WorkspaceRepoChangesetResponse:
        row = await self._required(changeset_id, for_update=True)
        prior_failure = (
            row.failure_detail if isinstance(row.failure_detail, dict) else {}
        )
        prior_provenance = (
            prior_failure.get("provenance")
            if isinstance(prior_failure.get("provenance"), dict)
            else {}
        )
        commit_message = prior_provenance.get("commit_message") or request.commit_message
        if not commit_message:
            return self._response(row)
        provenance = {
            "operator": str(prior_provenance.get("operator") or operator),
            "changeset_id": str(row.id),
            "commit_message": str(commit_message),
            "plan_id": prior_provenance.get("plan_id") or request.plan_id,
            "protected_main_source_sha": prior_provenance.get(
                "protected_main_source_sha"
            )
            or request.protected_main_source_sha,
        }
        if self.commit_writer is None:
            row.error = "verified GitHub App commit writer is not configured"
            row.failure_detail = {
                "phase": "git_closure",
                "state": "not_configured",
                "reason": "not_configured",
                "activation_preserved": True,
                "provenance": provenance,
            }
            await self.db.flush()
            await self.db.commit()
            return self._response(row)

        # Leave a durable pending marker before Git. If persisting the outcome
        # later fails, the active workspace is still explicit and reconcilable.
        candidate_commit_sha = prior_failure.get("commit_sha")
        row.failure_detail = {
            "phase": "git_closure",
            "state": "pending",
            "activation_preserved": True,
            "provenance": provenance,
        }
        if candidate_commit_sha:
            row.failure_detail["commit_sha"] = str(candidate_commit_sha)
        await self.db.flush()
        await self.db.commit()
        from src.core.workspace_writer import (
            WorkspaceWriterLeaseLost,
            checkpoint_workspace_writer_lease,
        )

        try:
            from src.services.workspace_convergence import snapshot_repo_storage

            expected_file_hashes = dict(row.authoritative_files or {})
            expected_parent_hashes = row.authoritative_base_files
            if expected_parent_hashes is None:
                raise ChangesetInvalid(
                    "Git closure has no durable authoritative base snapshot"
                )
            current = await snapshot_repo_storage(self.repo)
            if current.revision != row.authoritative_revision:
                raise ChangesetConflict(
                    "authoritative_snapshot_changed",
                    expected_revision=row.authoritative_revision,
                    current_revision=current.revision,
                    conflicting_paths=sorted(
                        path
                        for path in set(expected_file_hashes) | set(current.file_hashes)
                        if expected_file_hashes.get(path)
                        != current.file_hashes.get(path)
                    ),
                )
            files = tuple(
                PlatformCommitFile(
                    path=item["path"],
                    content_base64=(
                        item.get("content_base64")
                        if item.get("operation") == "write"
                        else None
                    ),
                    expected_before_sha256=expected_parent_hashes.get(item["path"]),
                    expected_sha256=expected_file_hashes.get(item["path"]),
                )
                for item in row.mutations
            )
            result = await self.commit_writer.write(
                PlatformCommitRequest(
                    commit_message=provenance["commit_message"],
                    operator=provenance["operator"],
                    changeset_id=row.id,
                    files=files,
                    expected_parent_files=dict(expected_parent_hashes),
                    expected_committed_files=expected_file_hashes,
                    plan_id=provenance.get("plan_id"),
                    protected_main_source_sha=provenance.get(
                        "protected_main_source_sha"
                    ),
                    candidate_commit_sha=(
                        str(candidate_commit_sha) if candidate_commit_sha else None
                    ),
                )
            )
            await checkpoint_workspace_writer_lease(self.db)
        except WorkspaceWriterLeaseLost:
            await self.db.rollback()
            raise
        except Exception as exc:
            await self.db.rollback()
            row = await self._required(changeset_id, for_update=True)
            row.error = str(exc)
            row.failure_detail = {
                "phase": "git_closure",
                "state": "failed",
                "message": str(exc),
                "activation_preserved": True,
                "provenance": provenance,
                "dirty_generation": row.dirty_generation,
            }
            commit_sha = (
                exc.commit_sha if isinstance(exc, PlatformCommitError) else None
            )
            if commit_sha:
                row.commit_sha = commit_sha
                row.status = "committed_unpushed"
                row.failure_detail["phase"] = "remote_verification"
                row.failure_detail["commit_sha"] = commit_sha
            await self.db.flush()
            await self.db.commit()
            return self._response(row)

        row = await self._required(changeset_id, for_update=True)
        row.commit_sha = result.commit_sha
        row.remote_sha = result.commit_sha
        row.status = "committed"
        row.error = None
        row.failure_detail = None
        row.closure_completed_at = datetime.now(timezone.utc)
        await self.db.flush()
        # Persist the externally observed Git outcome before touching the dirty
        # generation. If the runner dies after this commit, the closure ledger
        # still contains enough evidence for an idempotent retry/readback.
        await self.db.commit()

        if (
            request.push
            and row.status == "committed"
            and row.dirty_generation
        ):
            from src.core.repo_dirty import reconcile_repo_dirty

            owned_generation = row.dirty_generation
            reconciled = await reconcile_repo_dirty(owned_generation)
            if not reconciled:
                row = await self._required(changeset_id, for_update=True)
                row.failure_detail = {
                    "phase": "dirty_reconciliation",
                    "state": "preserved_newer_generation",
                    "dirty_generation": owned_generation,
                    "activation_preserved": True,
                }
                await self.db.flush()
                await self.db.commit()
        row = await self._required(changeset_id)
        return self._response(row)

    async def abort(self, changeset_id: UUID) -> WorkspaceRepoChangesetResponse:
        row = await self._required(changeset_id, for_update=True)
        if row.status not in self.ABORTABLE:
            raise ChangesetInvalid(
                f"changeset in {row.status!r} state cannot be aborted"
            )
        if row.activation_backup:
            backup = dict(row.activation_backup)
            mutations = {item["path"]: dict(item) for item in row.mutations}
            await self.db.commit()
            from src.core.workspace_writer import assert_workspace_writer_access

            # Hold the short direct-writer gate across recovery. Unlike normal
            # activation this is not a leased job, so releasing between files
            # would let a new writer race the restore preflight.
            await assert_workspace_writer_access(self.db)
            conflicting_paths: list[str] = []
            for path in backup:
                current = await self._read_optional(path)
                current_hash = (
                    hashlib.sha256(current).hexdigest()
                    if current is not None
                    else None
                )
                mutation = mutations.get(path, {})
                if current_hash not in {
                    mutation.get("before_hash"),
                    mutation.get("after_hash"),
                }:
                    conflicting_paths.append(path)
            if conflicting_paths:
                raise ChangesetConflict(
                    "abort_revision_mismatch",
                    conflicting_paths=sorted(conflicting_paths),
                )
            storage = FileStorageService(self.db)
            for path, encoded in backup.items():
                if encoded is None:
                    await storage.delete_file(path)
                else:
                    restored = await storage.write_file(
                        path,
                        base64.b64decode(encoded),
                        updated_by="changeset-abort",
                        force_deactivation=True,
                    )
                    if restored.pending_deactivations:
                        raise RuntimeError(
                            f"deactivation preflight blocked abort restore of {path}"
                        )
            row = await self._required(changeset_id, for_update=True)
            row.activation_backup = None
        elif row.status == "activating":
            failure = row.failure_detail if isinstance(row.failure_detail, dict) else {}
            if failure.get("phase") != "activation_snapshot":
                raise ChangesetInvalid(
                    "activating changeset cannot be safely aborted without its source backup"
                )
        row.status = "aborted"
        row.failure_detail = None
        await self.db.flush()
        return self._response(row)

    async def _required(
        self, changeset_id: UUID, *, for_update: bool = False
    ) -> WorkspaceRepoChangeset:
        row = await self.rows.get(
            changeset_id, self.organization_id, for_update=for_update
        )
        if row is None:
            raise KeyError(changeset_id)
        return row

    def _ensure_active(self, row: WorkspaceRepoChangeset) -> None:
        if row.status not in self.ACTIVE:
            raise ChangesetInvalid(
                f"changeset in {row.status!r} state cannot be modified"
            )

    async def _read_optional(self, path: str) -> bytes | None:
        if not await self.repo.exists(path):
            return None
        return await self.repo.read(path)

    @staticmethod
    def _unified_diff(
        path: str, before: bytes | None, after: bytes | None
    ) -> str | None:
        try:
            old = (before or b"").decode("utf-8").splitlines(keepends=True)
            new = (after or b"").decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError:
            return None
        return "".join(
            difflib.unified_diff(old, new, fromfile=f"a/{path}", tofile=f"b/{path}")
        )

    @staticmethod
    def _response(row: WorkspaceRepoChangeset) -> WorkspaceRepoChangesetResponse:
        return WorkspaceRepoChangesetResponse(
            id=row.id,
            scope=row.scope,
            base_revision=row.base_revision,
            status=cast(ChangesetStatus, row.status),
            title=row.title,
            worker_id=row.worker_id,
            mutations=[
                WorkspaceRepoMutation.model_validate(item) for item in row.mutations
            ],
            validation=row.validation,
            activated_revision=row.activated_revision,
            commit_sha=row.commit_sha,
            error=row.error,
            failure_detail=row.failure_detail,
            created_by=row.created_by,
            writer_job_id=row.writer_job_id,
            dirty_generation=row.dirty_generation,
            authoritative_revision=row.authoritative_revision,
            remote_sha=row.remote_sha,
            commit_message=row.commit_message,
            push_requested=bool(row.push_requested),
            closure_started_at=row.closure_started_at,
            closure_completed_at=row.closure_completed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


async def build_workspace_repo_changeset_service(
    db: AsyncSession,
    organization_id: UUID,
) -> WorkspaceRepoChangesetService:
    """Construct the service for HTTP enqueue checks and durable job runners."""
    from src.config import get_settings
    from src.services.github_config import get_github_config
    from src.services.platform_commit_writer import GitHubAppCommitWriter

    config = await get_github_config(db, organization_id)
    settings = get_settings()
    writer = None
    if config and config.repo_url and settings.github_app_commit_writer_configured:
        app_id = settings.github_app_id
        installation_id = settings.github_app_installation_id
        private_key = settings.github_app_private_key
        if app_id is None or installation_id is None or private_key is None:
            raise RuntimeError("GitHub App commit writer configuration is incomplete")
        writer = GitHubAppCommitWriter(
            repo_url=config.repo_url,
            branch=config.branch,
            app_id=app_id,
            installation_id=installation_id,
            private_key=private_key.get_secret_value(),
        )
    return WorkspaceRepoChangesetService(
        db,
        organization_id,
        commit_writer=writer,
    )


async def enqueue_workspace_repo_writer(
    db: AsyncSession,
    organization_id: UUID,
    changeset_id: UUID,
    request: WorkspaceRepoActivateRequest,
    operation: Literal["activate", "retry"],
    *,
    requested_by_user_id: UUID,
    requested_by_email: str,
    requested_by_name: str,
):
    """Validate and durably enqueue the global authoritative workspace writer."""
    from src.core.workspace_writer import (
        WORKSPACE_WRITER_RESOURCE_LOCK,
        lock_workspace_writer_gate,
    )
    from src.jobs.platform.workspace_repo_closure import (
        WORKSPACE_REPO_CLOSURE_DEFINITION,
        WorkspaceRepoClosurePayload,
    )
    from src.services.platform_jobs import (
        enqueue_platform_job,
        ensure_platform_job_notification,
        publish_platform_job_update,
    )

    service = await build_workspace_repo_changeset_service(db, organization_id)
    await lock_workspace_writer_gate(db)
    await service.prepare_writer_enqueue(changeset_id, request, operation)
    job, reused = await enqueue_platform_job(
        db,
        WORKSPACE_REPO_CLOSURE_DEFINITION,
        WorkspaceRepoClosurePayload(
            changeset_id=changeset_id,
            organization_id=organization_id,
            operation=operation,
            commit_message=request.commit_message,
            push=request.push,
            updated_by=requested_by_email,
            plan_id=request.plan_id,
            protected_main_source_sha=request.protected_main_source_sha,
        ),
        dedupe_key=f"{operation}:{changeset_id}",
        resource_lock_key=WORKSPACE_WRITER_RESOURCE_LOCK,
        priority=1000,
        organization_id=organization_id,
        requested_by_user_id=requested_by_user_id,
        requested_by_email=requested_by_email,
        requested_by_name=requested_by_name,
        resource_type="workspace_repo_changeset",
        resource_id=str(changeset_id),
        title=(
            f"Activating workspace changeset {changeset_id}"
            if operation == "activate"
            else f"Retrying workspace closure {changeset_id}"
        ),
        action_url="/diagnostics",
    )
    if reused and job.requested_by_user_id != str(requested_by_user_id):
        raise ChangesetConflict(
            "workspace_writer_active",
            message="This workspace changeset already has an active writer",
        )
    await service.assign_writer_job(changeset_id, job.id)
    if job.notification_id is None:
        try:
            await ensure_platform_job_notification(db, job)
        except Exception:
            logger.warning(
                "Workspace writer queued without a progress notification",
                extra={"platform_job_id": str(job.id)},
                exc_info=True,
            )
    await db.commit()
    await db.refresh(job)
    await publish_platform_job_update(job)
    return job, reused
