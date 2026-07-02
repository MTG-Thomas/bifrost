from __future__ import annotations

from src.services.solutions.source_artifact import SolutionSourceArtifactStorage


def _artifact_with_read(
    data: bytes | None,
) -> SolutionSourceArtifactStorage:
    artifact = object.__new__(SolutionSourceArtifactStorage)

    async def read() -> bytes | None:
        return data

    artifact.read = read  # type: ignore[method-assign]
    return artifact


async def test_copy_to_path_writes_existing_artifact(tmp_path) -> None:
    artifact = _artifact_with_read(b"source-zip")
    target = tmp_path / "source.zip"

    copied = await artifact.copy_to_path(target)

    assert copied is True
    assert target.read_bytes() == b"source-zip"


async def test_copy_to_path_returns_false_when_missing(tmp_path) -> None:
    artifact = _artifact_with_read(None)
    target = tmp_path / "source.zip"

    copied = await artifact.copy_to_path(target)

    assert copied is False
    assert not target.exists()
