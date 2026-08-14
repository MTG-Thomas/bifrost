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
import json
import logging
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import cast
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
    WorkspaceRepoGitConvergenceApplyRequest,
    WorkspaceRepoGitConvergenceChangeset,
    WorkspaceRepoGitConvergencePath,
    WorkspaceRepoGitConvergencePreviewRequest,
    WorkspaceRepoGitConvergenceResponse,
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
from src.services.workflow_registration import (
    WorkspaceRegistrationCandidate,
    WorkflowRegistrationConflict,
    apply_workspace_registration_plan,
    plan_workspace_registrations,
)

logger = logging.getLogger(__name__)

CANDIDATE_SCHEMA = "bifrost.workspace-candidate/v2"
GIT_CLOSURE_SCHEMA = "bifrost.workspace-git-closure/v1"
GIT_CONVERGENCE_SCHEMA = "bifrost.workspace-history-convergence/v1"


@dataclass
class _GitConvergencePlan:
    response: WorkspaceRepoGitConvergenceResponse
    files: tuple[PlatformCommitFile, ...]
    rows: list[WorkspaceRepoChangeset]


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


class WorkspaceRepoChangesetService:
    ACTIVE = {"open", "staged", "validated"}
    ABORTABLE = ACTIVE | {"conflicted"}
    RETRYABLE_GIT_FAILURES = {
        ("activated", "git_closure"),
        ("committed_unpushed", "git_push"),
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
        elif request.operation == "verify":
            if before_hash is None:
                raise ChangesetInvalid(
                    f"verify requires an existing workspace file: {path}"
                )
            after_hash = before_hash
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
            if item["operation"] == "write":
                after = base64.b64decode(item["content_base64"])
            elif item["operation"] == "verify":
                after = before
            else:
                after = None
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
        registration_candidates: list[WorkspaceRegistrationCandidate] = []
        deactivation = DeactivationProtectionService(self.db)
        parser = ASTMetadataParser()
        for item in row.mutations:
            path = item["path"]
            raw: bytes | None = None
            if item["operation"] == "verify":
                raw = await self._read_optional(path)
                current_hash = (
                    hashlib.sha256(raw).hexdigest() if raw is not None else None
                )
                if current_hash != item.get("before_hash"):
                    raise ChangesetConflict(
                        "file_revision_mismatch",
                        path=path,
                        expected_hash=item.get("before_hash"),
                        current_hash=current_hash,
                    )
            if not path.endswith(".py"):
                continue
            names: set[str] = set()
            decorator_info: dict[str, tuple[str, str]] = {}
            if item["operation"] == "write":
                raw = base64.b64decode(item["content_base64"])
            if raw is not None:
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
                            if kind == "data_provider":
                                workflow_type = "data_provider"
                            elif kind == "tool":
                                workflow_type = "tool"
                            else:
                                workflow_type = "workflow"
                            registration_candidates.append(
                                WorkspaceRegistrationCandidate(
                                    path=path,
                                    function_name=node.name,
                                    workflow_type=workflow_type,
                                    name=kwargs.get("name") or node.name,
                                    requested_id=kwargs.get("id"),
                                )
                            )
            if not item.get("force_deactivation"):
                found, _ = await deactivation.detect_pending_deactivations(
                    path=path,
                    new_function_names=names,
                    new_decorator_info=decorator_info,
                )
                pending.extend({**asdict(value), "path": path} for value in found)
        registration_actions, registry_diagnostics = await plan_workspace_registrations(
            self.db, self.organization_id, registration_candidates
        )
        diagnostics.extend(registry_diagnostics)
        has_source_mutations = any(
            item["operation"] != "verify" for item in row.mutations
        )
        has_registration_mutations = any(
            item.get("action") in {"create", "reactivate"}
            for item in registration_actions
        )
        if not has_source_mutations and not has_registration_mutations:
            diagnostics.append(
                {
                    "severity": "error",
                    "source": "no_op",
                    "message": (
                        "exact-byte verification found no source or registry mutation to activate"
                        if row.mutations
                        else "changeset contains no source or registry mutation to activate"
                    ),
                }
            )
        valid = (
            not diagnostics
            and not pending
            and bool(row.mutations)
            and (has_source_mutations or has_registration_mutations)
        )
        current_revision, _ = await self._snapshot(row.scope)
        candidate_id = self._candidate_id(
            row,
            validated_revision=current_revision,
            registration_actions=registration_actions,
        )
        result = WorkspaceRepoValidationResponse(
            valid=valid,
            candidate_id=candidate_id,
            diagnostics=diagnostics,
            pending_deactivations=pending,
            registration_actions=registration_actions,
            validated_revision=current_revision,
        )
        row.validation = result.model_dump(mode="json")
        row.status = "validated" if valid else "staged"
        await self.db.flush()
        return result

    async def activate(
        self, changeset_id: UUID, request: WorkspaceRepoActivateRequest, updated_by: str
    ) -> WorkspaceRepoChangesetResponse:
        if request.commit_message and not request.push:
            raise ChangesetInvalid("verified platform Git closure requires push=true")
        # PostgreSQL transaction-scoped serialization shared by all API replicas.
        await self.db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext('bifrost:workspace-repo-changesets'))"
            )
        )
        row = await self._required(changeset_id, for_update=True)
        has_source_mutations = any(
            item["operation"] != "verify" for item in row.mutations
        )
        if not has_source_mutations and (request.commit_message or request.push):
            raise ChangesetInvalid(
                "registration-only activation cannot request a Git source commit"
            )
        if (
            row.status != "validated"
            or not row.validation
            or not row.validation.get("valid")
        ):
            raise ChangesetInvalid(
                "changeset must pass validation immediately before activation"
            )
        candidate_id = str(row.validation.get("candidate_id") or "")
        if not candidate_id or request.candidate_id != candidate_id:
            raise ChangesetInvalid(
                "activation candidate_id must exactly match the latest validation"
            )
        current_revision, current_files = await self._snapshot(row.scope)
        conflicting = [
            item["path"]
            for item in row.mutations
            if current_files.get(item["path"]) != item.get("before_hash")
        ]
        if conflicting:
            row.status = "conflicted"
            await self.db.flush()
            await self.db.commit()
            raise ChangesetConflict(
                "revision_mismatch",
                base_revision=row.base_revision,
                current_revision=current_revision,
                conflicting_paths=conflicting,
            )
        row.status = "activating"
        originals = {
            item["path"]: await self._read_optional(item["path"])
            for item in row.mutations
            if item["operation"] != "verify"
        }
        storage = FileStorageService(self.db)
        python_paths = [
            item["path"]
            for item in row.mutations
            if item["operation"] != "verify" and item["path"].endswith(".py")
        ]
        from src.core.module_cache import workspace_source_update

        async with workspace_source_update(
            reason="workspace_changeset_activated",
            changed_paths=python_paths,
            broadcast=True,
        ):
            try:
                try:
                    applied_registrations = await apply_workspace_registration_plan(
                        self.db,
                        self.organization_id,
                        list(row.validation.get("registration_actions") or []),
                    )
                    row.validation = {
                        **row.validation,
                        "registration_actions": applied_registrations,
                    }
                except WorkflowRegistrationConflict as exc:
                    raise ChangesetInvalid(str(exc)) from exc
                for item in row.mutations:
                    if item["operation"] == "delete":
                        await storage.delete_file(item["path"])
                    elif item["operation"] == "write":
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
                activated_revision, activated_files = await self._snapshot(row.scope)
                file_evidence: list[dict] = []
                for item in sorted(row.mutations, key=lambda value: value["path"]):
                    observed_hash = activated_files.get(item["path"])
                    expected_hash = (
                        item.get("after_hash")
                        if item["operation"] in {"write", "verify"}
                        else None
                    )
                    if observed_hash != expected_hash:
                        raise ChangesetInvalid(
                            f"activation readback mismatch for {item['path']}"
                        )
                    file_evidence.append(
                        {
                            "path": item["path"],
                            "operation": item["operation"],
                            "sha256": observed_hash,
                        }
                    )
                row.activated_revision = activated_revision
                current_validation: dict[str, object] = dict(row.validation or {})
                row.validation = {
                    **current_validation,
                    "activation_evidence": {
                        "schema": CANDIDATE_SCHEMA,
                        "candidate_id": candidate_id,
                        "activated_revision": activated_revision,
                        "files": file_evidence,
                        "registration_actions": current_validation.get(
                            "registration_actions", []
                        ),
                    },
                }
                row.status = "activated"
                await self.db.flush()
                # Activation is the authoritative workspace transition. Make it
                # durable before crossing the independent Git boundary.
                await self.db.commit()
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
                    except Exception as rollback_exc:
                        rollback_errors.append(f"{path}: {rollback_exc}")
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

        if row.validation.get("registration_actions"):
            try:
                from src.services.mcp_server.server import refresh_workflow_tools

                await refresh_workflow_tools()
            except Exception as exc:
                logger.warning("Failed to refresh MCP workflow tools: %s", exc)

        return await self._complete_git_closure(
            changeset_id, request, operator=updated_by
        )

    async def retry_git_closure(
        self,
        changeset_id: UUID,
        request: WorkspaceRepoActivateRequest,
        operator: str,
    ) -> WorkspaceRepoChangesetResponse:
        """Retry only failed Git closure after workspace activation succeeded."""
        await self.db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext('bifrost:workspace-repo-changesets'))"
            )
        )
        row = await self._required(changeset_id, for_update=True)
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
        return await self._complete_git_closure(
            changeset_id, request, operator=operator
        )

    async def recoverable_git_closures(
        self, *, scope: str | None = None
    ) -> list[WorkspaceRepoChangesetResponse]:
        """List this organization's explicit failed Git closure records."""
        rows = await self.rows.list_retryable_git_failures(
            self.organization_id, scope=scope
        )
        return [self._response(row) for row in rows]

    async def preview_git_convergence(
        self, request: WorkspaceRepoGitConvergencePreviewRequest
    ) -> WorkspaceRepoGitConvergenceResponse:
        """Bind live, reviewed, and history state without mutating any of them."""
        rows = [await self._required(value) for value in request.changeset_ids]
        pending = self._pending_git_convergence_plan(rows)
        if pending is not None:
            return pending.response
        return (await self._build_git_convergence_plan(request)).response

    async def apply_git_convergence(
        self,
        request: WorkspaceRepoGitConvergenceApplyRequest,
        *,
        operator: str,
    ) -> WorkspaceRepoGitConvergenceResponse:
        """Write only the exact history paths bound by a reviewed preview."""
        await self.db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext('bifrost:workspace-repo-changesets'))"
            )
        )
        selected_rows = [
            await self._required(value, for_update=True)
            for value in request.changeset_ids
        ]
        plan = self._pending_git_convergence_plan(selected_rows)
        pending_retry = plan is not None
        if plan is None:
            plan = await self._build_git_convergence_plan(request, for_update=True)
        if request.candidate_id != plan.response.candidate_id:
            raise ChangesetConflict(
                "history_convergence_candidate_mismatch",
                expected_candidate_id=request.candidate_id,
                current_candidate_id=plan.response.candidate_id,
            )
        if not plan.response.ready_to_apply:
            raise ChangesetInvalid(
                "history convergence preview is not ready to apply"
            )
        if self.commit_writer is None:  # guarded in plan construction as well
            raise ChangesetInvalid("verified GitHub App commit writer is not configured")

        effective_operator = operator
        if pending_retry:
            stored = cast(dict, selected_rows[0].failure_detail)
            original_message = str(stored.get("commit_message") or "")
            if request.commit_message != original_message:
                raise ChangesetInvalid(
                    "pending convergence commit_message must match its immutable plan"
                )
            effective_operator = str(stored.get("operator") or operator)

        pending_evidence = {
            "phase": "git_convergence",
            "state": "pending",
            "activation_preserved": True,
            "candidate_id": plan.response.candidate_id,
            "protected_main_source_sha": plan.response.protected_main_source_sha,
            "history_parent_sha": plan.response.history_head_sha,
            "commit_message": request.commit_message,
            "operator": effective_operator,
        }
        primary_evidence_id = sorted((row.id for row in plan.rows), key=str)[0]
        if not all(
            isinstance(row.failure_detail, dict)
            and row.failure_detail.get("phase") == "git_convergence"
            and row.failure_detail.get("state") == "pending"
            and row.failure_detail.get("candidate_id") == plan.response.candidate_id
            for row in plan.rows
        ):
            for row in plan.rows:
                row.failure_detail = {
                    **pending_evidence,
                    "primary_evidence_changeset_id": str(primary_evidence_id),
                    **(
                        {
                            "plan": plan.response.model_dump(mode="json"),
                            "files": [asdict(value) for value in plan.files],
                        }
                        if row.id == primary_evidence_id
                        else {}
                    ),
                    "prior_failure": row.failure_detail,
                }
            await self.db.flush()
            # This durable marker makes an external Git success recoverable even
            # if the later database closeout is interrupted.
            await self.db.commit()

        commit_request = PlatformCommitRequest(
            commit_message=request.commit_message,
            operator=effective_operator,
            changeset_id=primary_evidence_id,
            files=plan.files,
            plan_id=plan.response.candidate_id,
            protected_main_source_sha=plan.response.protected_main_source_sha,
            expected_head_sha=plan.response.history_head_sha,
            convergence_candidate_id=plan.response.candidate_id,
            reconciled_changeset_ids=tuple(
                sorted((row.id for row in plan.rows), key=str)
            ),
        )
        try:
            result = await self.commit_writer.write(commit_request)
        except Exception as exc:
            await self.db.rollback()
            commit_sha = (
                exc.commit_sha if isinstance(exc, PlatformCommitError) else None
            )
            for changeset_id in request.changeset_ids:
                row = await self._required(changeset_id, for_update=True)
                prior_failure = row.failure_detail
                row.error = str(exc)
                row.failure_detail = {
                    "phase": "git_convergence",
                    "state": "failed",
                    "message": str(exc),
                    "activation_preserved": True,
                    "candidate_id": plan.response.candidate_id,
                    "protected_main_source_sha": (
                        plan.response.protected_main_source_sha
                    ),
                    "history_parent_sha": plan.response.history_head_sha,
                    "commit_message": request.commit_message,
                    "operator": effective_operator,
                    "primary_evidence_changeset_id": str(primary_evidence_id),
                    **(
                        {
                            "plan": plan.response.model_dump(mode="json"),
                            "files": [asdict(value) for value in plan.files],
                        }
                        if row.id == primary_evidence_id
                        else {}
                    ),
                    "prior_failure": prior_failure,
                }
                if commit_sha:
                    row.commit_sha = commit_sha
                    row.failure_detail["commit_sha"] = commit_sha
            await self.db.flush()
            await self.db.commit()
            return plan.response.model_copy(
                update={
                    "ready_to_apply": False,
                    "diagnostics": [
                        *plan.response.diagnostics,
                        {
                            "severity": "error",
                            "source": "git_convergence",
                            "message": str(exc),
                            "commit_sha": commit_sha,
                        },
                    ],
                    "commit_sha": commit_sha,
                }
            )

        dispositions = {
            item.changeset_id: item for item in plan.response.changesets
        }
        for row in plan.rows:
            disposition = dispositions[row.id]
            current_validation = dict(row.validation or {})
            row.validation = {
                **current_validation,
                "history_convergence": {
                    "schema": GIT_CONVERGENCE_SCHEMA,
                    "candidate_id": plan.response.candidate_id,
                    "protected_main_source_sha": (
                        plan.response.protected_main_source_sha
                    ),
                    "history_parent_sha": plan.response.history_head_sha,
                    "commit_sha": result.commit_sha,
                    "signature_state": result.signature_state,
                    "operator": effective_operator,
                    "selected_changeset_ids": [
                        str(value)
                        for value in sorted(request.changeset_ids, key=str)
                    ],
                    "disposition": disposition.disposition,
                    "reconciled_paths": disposition.reconciled_paths,
                    "superseded_paths": disposition.superseded_paths,
                },
            }
            row.commit_sha = result.commit_sha
            row.status = "committed"
            row.error = None
            row.failure_detail = None
        await self.db.flush()
        await self.db.commit()
        return plan.response.model_copy(
            update={
                "applied": True,
                "commit_sha": result.commit_sha,
                "signature_state": result.signature_state,
            }
        )

    def _pending_git_convergence_plan(
        self, rows: list[WorkspaceRepoChangeset]
    ) -> _GitConvergencePlan | None:
        if not rows:
            return None
        details = [
            row.failure_detail if isinstance(row.failure_detail, dict) else {}
            for row in rows
        ]
        candidate_ids = {str(item.get("candidate_id") or "") for item in details}
        primary_ids = {
            str(item.get("primary_evidence_changeset_id") or "")
            for item in details
        }
        if (
            len(candidate_ids) != 1
            or "" in candidate_ids
            or len(primary_ids) != 1
            or "" in primary_ids
            or not all(
                item.get("phase") == "git_convergence"
                and (
                    item.get("state") == "pending"
                    or (
                        item.get("state") == "failed"
                        and bool(item.get("commit_sha"))
                    )
                )
                for item in details
            )
        ):
            return None
        primary_id = next(iter(primary_ids))
        evidence = [
            item
            for row, item in zip(rows, details, strict=True)
            if str(row.id) == primary_id
        ]
        if len(evidence) != 1 or not isinstance(evidence[0].get("plan"), dict):
            raise ChangesetInvalid(
                "pending convergence primary evidence is missing"
            )
        response = WorkspaceRepoGitConvergenceResponse.model_validate(
            evidence[0]["plan"]
        )
        if {item.changeset_id for item in response.changesets} != {
            row.id for row in rows
        }:
            raise ChangesetInvalid(
                "pending convergence must resume with its exact selected changesets"
            )
        file_evidence = evidence[0].get("files")
        if not isinstance(file_evidence, list) or not file_evidence:
            raise ChangesetInvalid(
                "pending convergence exact file evidence is missing"
            )
        try:
            files = [PlatformCommitFile(**item) for item in file_evidence]
        except (TypeError, KeyError) as exc:
            raise ChangesetInvalid(
                "pending convergence exact file evidence is invalid"
            ) from exc
        return _GitConvergencePlan(
            response=response,
            files=tuple(files),
            rows=rows,
        )

    async def _build_git_convergence_plan(
        self,
        request: WorkspaceRepoGitConvergencePreviewRequest,
        *,
        for_update: bool = False,
    ) -> _GitConvergencePlan:
        if self.commit_writer is None:
            raise ChangesetInvalid("verified GitHub App commit writer is not configured")

        rows = [
            await self._required(changeset_id, for_update=for_update)
            for changeset_id in request.changeset_ids
        ]
        entries_by_path: dict[str, list[tuple[WorkspaceRepoChangeset, dict]]] = {}
        total_source_bytes = 0
        for row in rows:
            failure = row.failure_detail if isinstance(row.failure_detail, dict) else {}
            retry_key = (row.status, str(failure.get("phase") or ""))
            convergence_retry = (
                row.status == "activated"
                and failure.get("phase") == "git_convergence"
                and failure.get("state") in RETRYABLE_GIT_FAILURE_STATES
            )
            if (
                retry_key not in self.RETRYABLE_GIT_FAILURES
                and not convergence_retry
            ):
                raise ChangesetInvalid(
                    f"changeset {row.id} is not an activated recoverable Git closure"
                )
            source_mutations = [
                item for item in row.mutations if item.get("operation") != "verify"
            ]
            if not source_mutations:
                raise ChangesetInvalid(
                    f"changeset {row.id} has no source mutation to reconcile"
                )
            for item in source_mutations:
                if item.get("operation") != "write":
                    raise ChangesetInvalid(
                        "history convergence currently accepts exact-byte writes only"
                    )
                path = str(item.get("path") or "")
                content_base64 = item.get("content_base64")
                after_hash = item.get("after_hash")
                if not path or not content_base64 or not after_hash:
                    raise ChangesetInvalid(
                        f"changeset {row.id} contains incomplete source evidence"
                    )
                try:
                    content = base64.b64decode(content_base64, validate=True)
                except Exception as exc:
                    raise ChangesetInvalid(
                        f"changeset {row.id} contains invalid source evidence"
                    ) from exc
                if hashlib.sha256(content).hexdigest() != after_hash:
                    raise ChangesetInvalid(
                        f"changeset {row.id} source evidence hash does not match"
                    )
                total_source_bytes += len(content)
                entries_by_path.setdefault(path, []).append((row, item))

        paths = tuple(sorted(entries_by_path))
        if len(paths) > 100:
            raise ChangesetInvalid(
                "history convergence is limited to 100 exact selected paths"
            )
        if total_source_bytes > 5 * 1024 * 1024:
            raise ChangesetInvalid(
                "history convergence is limited to 5 MiB of selected source evidence"
            )
        source_sha = request.protected_main_source_sha.lower()
        try:
            reviewed = await self.commit_writer.inspect(
                paths, ref=source_sha, reachable_from="main"
            )
            history = await self.commit_writer.inspect(paths)
        except PlatformCommitError as exc:
            raise ChangesetInvalid(str(exc)) from exc

        diagnostics: list[dict] = []
        if reviewed.commit_sha.lower() != source_sha:
            diagnostics.append(
                {
                    "severity": "error",
                    "source": "protected_main",
                    "message": "protected-main source SHA resolved to a different commit",
                    "expected": source_sha,
                    "observed": reviewed.commit_sha,
                }
            )

        path_results: list[WorkspaceRepoGitConvergencePath] = []
        files: list[PlatformCommitFile] = []
        source_by_path: dict[str, UUID | None] = {}
        desired_by_path: dict[str, str] = {}
        for path in paths:
            raw = await self._read_optional(path)
            live_hash = hashlib.sha256(raw).hexdigest() if raw is not None else None
            reviewed_hash = reviewed.file_sha256.get(path)
            history_hash = history.file_sha256.get(path)
            entries = entries_by_path[path]
            matching = [
                (row, item)
                for row, item in entries
                if item.get("after_hash") == live_hash == reviewed_hash
            ]
            evidence_row, evidence_item = max(
                matching or entries,
                key=lambda value: (
                    value[0].created_at,
                    str(value[0].id),
                ),
            )
            desired_hash = str(live_hash or evidence_item["after_hash"])
            source_by_path[path] = evidence_row.id if matching else None
            desired_by_path[path] = desired_hash
            if live_hash != reviewed_hash:
                diagnostics.append(
                    {
                        "severity": (
                            "warning"
                            if live_hash is not None and history_hash == live_hash
                            else "error"
                        ),
                        "source": "live_vs_reviewed",
                        "path": path,
                        "live_sha256": live_hash,
                        "reviewed_sha256": reviewed_hash,
                        "history_sha256": history_hash,
                    }
                )
            if live_hash is None:
                diagnostics.append(
                    {
                        "severity": "error",
                        "source": "live_source",
                        "path": path,
                        "message": "exact-byte history convergence does not support deletion",
                    }
                )
            requires_write = history_hash != desired_hash
            path_results.append(
                WorkspaceRepoGitConvergencePath(
                    path=path,
                    desired_sha256=desired_hash,
                    live_sha256=live_hash,
                    reviewed_sha256=reviewed_hash,
                    history_sha256=history_hash,
                    requires_write=requires_write,
                    source_changeset_id=evidence_row.id if matching else None,
                )
            )
            if requires_write:
                files.append(
                    PlatformCommitFile(
                        path=path,
                        content_base64=(
                            base64.b64encode(raw).decode() if raw is not None else None
                        ),
                        expected_before_sha256=history_hash,
                        expected_sha256=desired_hash,
                    )
                )

        changeset_results: list[WorkspaceRepoGitConvergenceChangeset] = []
        for row in sorted(rows, key=lambda value: str(value.id)):
            reconciled_paths: list[str] = []
            superseded_paths: list[str] = []
            for item in row.mutations:
                if item.get("operation") == "verify":
                    continue
                path = str(item["path"])
                if item.get("after_hash") == desired_by_path[path]:
                    reconciled_paths.append(path)
                else:
                    superseded_paths.append(path)
            if reconciled_paths and superseded_paths:
                disposition = "partially_superseded"
            elif superseded_paths:
                disposition = "superseded"
            else:
                disposition = "reconciled"
            changeset_results.append(
                WorkspaceRepoGitConvergenceChangeset(
                    changeset_id=row.id,
                    disposition=disposition,
                    reconciled_paths=sorted(reconciled_paths),
                    superseded_paths=sorted(superseded_paths),
                )
            )

        if not files:
            diagnostics.append(
                {
                    "severity": "error",
                    "source": "no_op",
                    "message": "selected paths are already present in production-live history",
                }
            )
        payload = {
            "schema": GIT_CONVERGENCE_SCHEMA,
            "changeset_ids": [str(value) for value in sorted(request.changeset_ids, key=str)],
            "protected_main": {
                "source_sha": source_sha,
                "resolved_sha": reviewed.commit_sha,
                "tree_sha": reviewed.tree_sha,
                "file_sha256": reviewed.file_sha256,
            },
            "live_file_sha256": {
                item.path: item.live_sha256 for item in path_results
            },
            "history": {
                "head_sha": history.commit_sha,
                "tree_sha": history.tree_sha,
                "file_sha256": history.file_sha256,
            },
            "desired_file_sha256": desired_by_path,
            "source_changeset_by_path": {
                path: str(value) if value is not None else None
                for path, value in source_by_path.items()
            },
            "dispositions": [
                item.model_dump(mode="json") for item in changeset_results
            ],
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        candidate_id = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
        response = WorkspaceRepoGitConvergenceResponse(
            ready_to_apply=not any(
                item.get("severity") == "error" for item in diagnostics
            ),
            candidate_id=candidate_id,
            protected_main_source_sha=source_sha,
            protected_main_tree_sha=reviewed.tree_sha,
            history_head_sha=history.commit_sha,
            history_tree_sha=history.tree_sha,
            diagnostics=diagnostics,
            paths=path_results,
            changesets=changeset_results,
        )
        return _GitConvergencePlan(
            response=response,
            files=tuple(files),
            rows=rows,
        )

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
        commit_message = (
            prior_provenance.get("commit_message") or request.commit_message
        )
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
        history_evidence: dict[str, object] | None = None
        try:
            source_mutations = tuple(
                item for item in row.mutations if item.get("operation") != "verify"
            )
            paths = tuple(sorted(str(item["path"]) for item in source_mutations))
            history = await self.commit_writer.inspect(paths)
            if history.signature_state != "VALID":
                raise PlatformCommitError(
                    "production-live history head is not a verified GitHub-signed commit"
                )

            classifications: list[dict[str, object]] = []
            files: list[PlatformCommitFile] = []
            superseded_paths: list[str] = []
            divergent_paths: list[str] = []
            for item in source_mutations:
                path = str(item["path"])
                before_hash = item.get("before_hash")
                target_hash = (
                    item.get("after_hash") if item.get("operation") == "write" else None
                )
                history_hash = history.file_sha256.get(path)
                if history_hash == target_hash:
                    disposition = "target"
                    superseded_paths.append(path)
                elif history_hash == before_hash:
                    disposition = "before"
                    files.append(
                        PlatformCommitFile(
                            path=path,
                            content_base64=(
                                item.get("content_base64")
                                if item.get("operation") == "write"
                                else None
                            ),
                            expected_before_sha256=before_hash,
                            expected_sha256=target_hash,
                        )
                    )
                else:
                    disposition = "other"
                    divergent_paths.append(path)
                classifications.append(
                    {
                        "path": path,
                        "disposition": disposition,
                        "before_sha256": before_hash,
                        "target_sha256": target_hash,
                        "history_sha256": history_hash,
                    }
                )

            history_evidence = {
                "schema": GIT_CLOSURE_SCHEMA,
                "history_head_sha": history.commit_sha,
                "history_tree_sha": history.tree_sha,
                "signature_state": history.signature_state,
                "paths": classifications,
            }
            if divergent_paths:
                raise PlatformCommitError(
                    "production-live history contains bytes outside the reviewed "
                    f"before/target states for: {', '.join(sorted(divergent_paths))}"
                )
            if candidate_commit_sha and files:
                raise PlatformCommitError(
                    "recorded Git candidate is not the current production-live target; "
                    "use guarded history convergence"
                )
            if not files:
                row = await self._required(changeset_id, for_update=True)
                current_validation = dict(row.validation or {})
                row.validation = {
                    **current_validation,
                    "git_closure": {
                        **history_evidence,
                        "disposition": "superseded",
                        "commit_sha": history.commit_sha,
                        "superseded_paths": sorted(superseded_paths),
                        "committed_paths": [],
                    },
                }
                row.commit_sha = history.commit_sha
                row.status = "committed"
                row.error = None
                row.failure_detail = None
                await self.db.flush()
                await self.db.commit()
                return self._response(row)

            result = await self.commit_writer.write(
                PlatformCommitRequest(
                    commit_message=provenance["commit_message"],
                    operator=provenance["operator"],
                    changeset_id=row.id,
                    files=tuple(files),
                    plan_id=provenance.get("plan_id"),
                    protected_main_source_sha=provenance.get(
                        "protected_main_source_sha"
                    ),
                    candidate_commit_sha=(
                        str(candidate_commit_sha) if candidate_commit_sha else None
                    ),
                    expected_head_sha=history.commit_sha,
                )
            )
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
            }
            if history_evidence is not None:
                row.failure_detail["history"] = history_evidence
            commit_sha = (
                exc.commit_sha if isinstance(exc, PlatformCommitError) else None
            )
            if commit_sha:
                row.commit_sha = commit_sha
                row.failure_detail["commit_sha"] = commit_sha
            await self.db.flush()
            await self.db.commit()
            return self._response(row)

        row = await self._required(changeset_id, for_update=True)
        current_validation = dict(row.validation or {})
        row.validation = {
            **current_validation,
            "git_closure": {
                **(history_evidence or {}),
                "disposition": (
                    "partially_superseded" if superseded_paths else "reconciled"
                ),
                "commit_sha": result.commit_sha,
                "signature_state": result.signature_state,
                "superseded_paths": sorted(superseded_paths),
                "committed_paths": sorted(item.path for item in files),
            },
        }
        row.commit_sha = result.commit_sha
        row.status = "committed"
        row.error = None
        row.failure_detail = None
        await self.db.flush()
        await self.db.commit()
        return self._response(row)

    async def abort(self, changeset_id: UUID) -> WorkspaceRepoChangesetResponse:
        row = await self._required(changeset_id, for_update=True)
        if row.status not in self.ABORTABLE:
            raise ChangesetInvalid(
                f"changeset in {row.status!r} state cannot be aborted"
            )
        row.status = "aborted"
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

    @staticmethod
    def _candidate_id(
        row: WorkspaceRepoChangeset,
        *,
        validated_revision: str,
        registration_actions: list[dict],
    ) -> str:
        """Hash the exact source CAS and registry intent approved for activation."""
        payload = {
            "schema": CANDIDATE_SCHEMA,
            "scope": row.scope,
            "base_revision": row.base_revision,
            "validated_revision": validated_revision,
            "mutations": [
                {
                    "path": item["path"],
                    "operation": item["operation"],
                    "before_hash": item.get("before_hash"),
                    "after_hash": item.get("after_hash"),
                    "force_deactivation": bool(item.get("force_deactivation")),
                }
                for item in sorted(row.mutations, key=lambda value: value["path"])
            ],
            "registration_actions": sorted(
                registration_actions,
                key=lambda item: (
                    str(item.get("path") or ""),
                    str(item.get("function_name") or ""),
                    str(item.get("type") or ""),
                    str(item.get("action") or ""),
                ),
            ),
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

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
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
