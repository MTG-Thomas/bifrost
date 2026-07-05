from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from src.models.contracts.solutions import SolutionExportOptions
from src.models.orm.solution_export_jobs import SolutionExportJob
from src.models.orm.solutions import Solution
from src.services.solutions import export_jobs


def _solution(*, version: str | None = "1.2.3") -> Solution:
    return Solution(id=uuid4(), slug="customer-portal", name="Customer Portal", version=version)


def _options(**overrides) -> SolutionExportOptions:
    values = {
        "include_configs": True,
        "include_secrets": False,
        "include_tables": False,
        "include_files": False,
        "password": None,
    }
    values.update(overrides)
    return SolutionExportOptions(**values)


def test_export_option_helpers_preserve_backup_semantics() -> None:
    options = _options(include_configs=False, include_secrets=True, include_tables=True, include_files=True)

    assert export_jobs.export_options_to_capture_flags(options) == {
        "include_imports": True,
        "include_values": True,
        "include_data": True,
        "include_files": True,
    }
    assert export_jobs.export_options_select_runtime_payload(options) is True
    assert export_jobs.export_artifact_filename(_solution()) == "customer-portal-1.2.3.zip"
    assert export_jobs.export_artifact_filename(_solution(version=None)) == "customer-portal-unversioned.zip"


def test_config_value_filtering_honors_config_and_secret_toggles() -> None:
    values = {"API_URL": "https://example.test", "API_TOKEN": "secret"}
    secret_keys = {"API_TOKEN"}

    assert export_jobs.filter_config_values_by_options(
        values,
        secret_keys=secret_keys,
        options=_options(include_configs=True, include_secrets=True),
    ) is values
    assert export_jobs.filter_config_values_by_options(
        values,
        secret_keys=secret_keys,
        options=_options(include_configs=True, include_secrets=False),
    ) == {"API_URL": "https://example.test"}
    assert export_jobs.filter_config_values_by_options(
        values,
        secret_keys=secret_keys,
        options=_options(include_configs=False, include_secrets=True),
    ) == {"API_TOKEN": "secret"}


def test_export_options_encrypt_round_trip_and_password_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_runtime = _options(include_tables=True)
    monkeypatch.setattr(export_jobs, "encrypt_secret", lambda value: f"enc:{value}")
    monkeypatch.setattr(
        export_jobs,
        "decrypt_secret",
        lambda value: value.removeprefix("enc:"),
    )

    encrypted = export_jobs.encrypt_export_options(selected_runtime)

    assert encrypted != selected_runtime.model_dump_json()
    assert export_jobs.decrypt_export_options(encrypted) == selected_runtime
    with pytest.raises(ValueError, match="requires a password"):
        export_jobs.validate_export_options_password(selected_runtime)
    export_jobs.validate_export_options_password(
        _options(include_tables=True, password="correct horse battery staple")
    )


def test_public_job_download_url_requires_completed_unexpired_artifact() -> None:
    now = datetime.now(timezone.utc)
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    expired = datetime.now(timezone.utc) - timedelta(minutes=5)
    completed = SolutionExportJob(
        id=uuid4(),
        solution_id=uuid4(),
        status="completed",
        progress_percent=100,
        artifact_storage_key="solution-exports/solution/job.zip",
        expires_at=future,
        created_at=now,
        updated_at=now,
    )
    failed = SolutionExportJob(
        id=uuid4(),
        solution_id=completed.solution_id,
        status="failed",
        progress_percent=100,
        artifact_storage_key="solution-exports/solution/job.zip",
        expires_at=future,
        created_at=now,
        updated_at=now,
    )
    stale = SolutionExportJob(
        id=uuid4(),
        solution_id=completed.solution_id,
        status="completed",
        progress_percent=100,
        artifact_storage_key="solution-exports/solution/job.zip",
        expires_at=expired,
        created_at=now,
        updated_at=now,
    )

    public = export_jobs.public_job(completed)

    assert public.download_url == f"/api/solutions/export-jobs/{completed.id}/download"
    assert export_jobs.public_job(failed).download_url is None
    assert export_jobs.public_job(stale).download_url is None


async def test_upload_export_artifact_streams_path_chunks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    artifact = tmp_path / "backup.zip"
    artifact.write_bytes(b"abcdef")
    observed: list[bytes] = []

    class _Storage:
        def __init__(self, db):
            self.db = db

        async def write_raw_chunks_to_s3(self, storage_key, chunks, *, content_type):
            assert storage_key == "solution-exports/s/job.zip"
            assert content_type == export_jobs.SOLUTION_EXPORT_ARTIFACT_CONTENT_TYPE
            async for chunk in chunks:
                observed.append(chunk)
            return "hash", sum(len(chunk) for chunk in observed)

    monkeypatch.setattr(export_jobs, "FileStorageService", _Storage)

    assert await export_jobs.upload_solution_export_artifact(
        object(), "solution-exports/s/job.zip", artifact
    ) == ("hash", 6)
    assert observed == [b"abcdef"]


async def test_delete_export_artifact_skips_missing_key_and_deletes_present_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted: list[str] = []

    class _Storage:
        def __init__(self, db):
            self.db = db

        async def delete_raw_from_s3(self, storage_key):
            deleted.append(storage_key)

    monkeypatch.setattr(export_jobs, "FileStorageService", _Storage)

    await export_jobs.delete_solution_export_artifact(object(), None)
    await export_jobs.delete_solution_export_artifact(object(), "solution-exports/s/job.zip")

    assert deleted == ["solution-exports/s/job.zip"]


async def test_artifact_service_delegates_build_upload_delete_and_static_helpers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db = object()
    service = export_jobs.SolutionExportArtifactService(db)
    solution = _solution()
    options = _options(password="pw")
    dest = tmp_path / "backup.zip"
    calls: list[tuple[str, object]] = []

    async def build_to_path(db_arg, solution_arg, options_arg, dest_arg):
        calls.append(("build_to_path", (db_arg, solution_arg, options_arg, dest_arg)))

    async def build_tempfile(db_arg, solution_arg, options_arg):
        calls.append(("build_tempfile", (db_arg, solution_arg, options_arg)))
        return dest

    async def upload(db_arg, storage_key, artifact_path):
        calls.append(("upload", (db_arg, storage_key, artifact_path)))
        return "hash", 10

    async def delete(db_arg, storage_key):
        calls.append(("delete", (db_arg, storage_key)))

    monkeypatch.setattr(export_jobs, "build_solution_backup_zip_to_path", build_to_path)
    monkeypatch.setattr(export_jobs, "build_solution_backup_zip_tempfile", build_tempfile)
    monkeypatch.setattr(export_jobs, "upload_solution_export_artifact", upload)
    monkeypatch.setattr(export_jobs, "delete_solution_export_artifact", delete)

    await service.build_zip_to_path(solution, options, dest)
    assert await service.build_zip_tempfile(solution, options) == dest
    assert await service.upload_artifact("solution-exports/s/job.zip", dest) == ("hash", 10)
    await service.delete_artifact("solution-exports/s/job.zip")

    assert [name for name, _ in calls] == ["build_to_path", "build_tempfile", "upload", "delete"]
    assert service.artifact_storage_key(solution.id, "job") == f"solution-exports/{solution.id}/job.zip"
    assert service.artifact_filename(solution) == "customer-portal-1.2.3.zip"
    assert service.capture_flags(options)["include_imports"] is True
