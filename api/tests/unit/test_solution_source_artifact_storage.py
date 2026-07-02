from __future__ import annotations

from src.services.solutions.source_artifact import SolutionSourceArtifactStorage


class _FakeArtifact(SolutionSourceArtifactStorage):
    def __init__(self, data: bytes | None) -> None:
        self._data = data

    async def read(self) -> bytes | None:
        return self._data


async def test_copy_to_path_writes_existing_artifact(tmp_path) -> None:
    artifact = _FakeArtifact(b"source-zip")
    target = tmp_path / "source.zip"

    copied = await artifact.copy_to_path(target)

    assert copied is True
    assert target.read_bytes() == b"source-zip"


async def test_copy_to_path_returns_false_when_missing(tmp_path) -> None:
    artifact = _FakeArtifact(None)
    target = tmp_path / "source.zip"

    copied = await artifact.copy_to_path(target)

    assert copied is False
    assert not target.exists()
