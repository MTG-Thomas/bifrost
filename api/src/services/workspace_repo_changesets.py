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
from dataclasses import asdict
from pathlib import PurePosixPath
from typing import Awaitable, Callable, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.contracts.workspace_repo_changesets import (
    WorkspaceRepoActivateRequest,
    ChangesetStatus,
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
from src.repositories.workspace_repo_changesets import WorkspaceRepoChangesetRepository
from src.services.file_storage import FileStorageService
from src.services.file_storage.ast_parser import ASTMetadataParser
from src.services.file_storage.deactivation import DeactivationProtectionService
from src.services.repo_storage import RepoStorage


class ChangesetConflict(Exception):
    def __init__(self, reason: str, **detail):
        self.detail = {"reason": reason, **detail}
        super().__init__(reason)


class ChangesetInvalid(Exception):
    pass


CommitCallback = Callable[[str, bool], Awaitable[tuple[str | None, str | None]]]


class WorkspaceRepoChangesetService:
    ACTIVE = {"open", "staged", "validated"}

    def __init__(
        self,
        db: AsyncSession,
        organization_id: UUID,
        repo: RepoStorage | None = None,
        commit_callback: CommitCallback | None = None,
    ):
        self.db = db
        self.organization_id = organization_id
        self.repo = repo or RepoStorage()
        self.rows = WorkspaceRepoChangesetRepository(db)
        self.commit_callback = commit_callback

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
        self, changeset_id: UUID, request: WorkspaceRepoActivateRequest, updated_by: str
    ) -> WorkspaceRepoChangesetResponse:
        # PostgreSQL transaction-scoped serialization shared by all API replicas.
        await self.db.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext('bifrost:workspace-repo-changesets'))"
            )
        )
        row = await self._required(changeset_id, for_update=True)
        if (
            row.status != "validated"
            or not row.validation
            or not row.validation.get("valid")
        ):
            raise ChangesetInvalid(
                "changeset must pass validation immediately before activation"
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
        }
        storage = FileStorageService(self.db)
        try:
            for item in row.mutations:
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
            activated_revision, _ = await self._snapshot(row.scope)
            row.activated_revision = activated_revision
            row.status = "activated"
            if request.commit_message:
                if self.commit_callback is None:
                    raise ChangesetInvalid(
                        "platform Git commit closure is not configured"
                    )
                row.commit_sha, push_error = await self.commit_callback(
                    request.commit_message, request.push
                )
                row.status = "committed_unpushed" if push_error else "committed"
                row.error = push_error
            await self.db.flush()
            return self._response(row)
        except Exception as exc:
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
            row.status = "failed"
            row.error = str(exc)
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

    async def abort(self, changeset_id: UUID) -> WorkspaceRepoChangesetResponse:
        row = await self._required(changeset_id, for_update=True)
        self._ensure_active(row)
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
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
