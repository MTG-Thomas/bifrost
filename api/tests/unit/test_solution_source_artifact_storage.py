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


async def test_write_path_streams_exact_artifact(tmp_path) -> None:
    artifact = object.__new__(SolutionSourceArtifactStorage)
    artifact.prefix = "_solution_artifacts/example/"
    captured = {}

    class Storage:
        async def put_object_from_chunks(self, key, chunks, *, content_type):
            captured["key"] = key
            captured["content_type"] = content_type
            captured["data"] = b"".join([chunk async for chunk in chunks])
            return "digest", len(captured["data"])

    artifact._storage = Storage()
    source = tmp_path / "source.zip"
    source.write_bytes(b"exact-source-artifact")

    await artifact.write_path(source)

    assert captured == {
        "key": "_solution_artifacts/example/source.zip",
        "content_type": "application/zip",
        "data": b"exact-source-artifact",
    }
