"""CLI download package version normalization."""

import io
import tarfile
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from src.routers import cli
from src.routers.cli import _to_pep440


def test_to_pep440_preserves_dev_release_versions() -> None:
    assert _to_pep440("1.0.8-dev.11") == "1.0.8.dev11"
    assert _to_pep440("v1.0.8-dev.11") == "1.0.8.dev11"


def test_to_pep440_preserves_dirty_dev_release_versions() -> None:
    assert _to_pep440("1.0.8-dev.11-dirty") == "1.0.8.dev11+dirty"


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("", "0.0.0"),
        ("unknown", "0.0.0"),
        ("v1.2.3", "1.2.3"),
        ("v1.2.3-dirty", "1.2.3+dirty"),
        ("v0.6-219-g24b8acb9", "0.6.post219+g24b8acb9"),
        ("v0.6-219-g24b8acb9-dirty", "0.6.post219+g24b8acb9.dirty"),
        ("abc1234", "0.0.0+gabc1234"),
        ("abc1234-dirty", "0.0.0+gabc1234.dirty"),
    ],
)
def test_to_pep440_covers_git_describe_and_fallback_shapes(version, expected) -> None:
    assert _to_pep440(version) == expected


@pytest.mark.asyncio
async def test_download_cli_stamps_version_and_excludes_platform_only_files(tmp_path) -> None:
    package_dir = tmp_path / "bifrost"
    package_dir.mkdir()
    (package_dir / "pyproject.toml").write_text(
        '[project]\nname = "bifrost-sdk"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    (package_dir / "__init__.py").write_text(
        "def _compute_version():\n    return 'dev'\n__version__ = _compute_version()\n",
        encoding="utf-8",
    )
    (package_dir / "cli.py").write_text("print('ok')\n", encoding="utf-8")
    (package_dir / "_internal.py").write_text("secret\n", encoding="utf-8")
    (package_dir / "README.md").write_text("ignored\n", encoding="utf-8")

    fake_file = tmp_path / "src" / "routers" / "cli.py"
    with (
        patch.object(cli, "__file__", str(fake_file)),
        patch("shared.version.get_version", return_value="v1.2.3-4-gabc1234"),
    ):
        response = await cli.download_cli()

    assert response.media_type == "application/gzip"
    assert response.headers["content-disposition"] == (
        "attachment; filename=bifrost-cli-v1.2.3-4-gabc1234.tar.gz"
    )

    with tarfile.open(fileobj=io.BytesIO(response.body), mode="r:gz") as tar:
        names = set(tar.getnames())
        pyproject = tar.extractfile("pyproject.toml").read().decode("utf-8")
        init_py = tar.extractfile("bifrost/__init__.py").read().decode("utf-8")

    assert "version = \"1.2.3.post4+gabc1234\"" in pyproject
    assert '__version__ = "v1.2.3-4-gabc1234"' in init_py
    assert "bifrost/cli.py" in names
    assert "bifrost/_internal.py" not in names
    assert "bifrost/README.md" not in names


@pytest.mark.asyncio
async def test_download_cli_reports_missing_package(tmp_path) -> None:
    fake_file = tmp_path / "src" / "routers" / "cli.py"

    with patch.object(cli, "__file__", str(fake_file)):
        with pytest.raises(HTTPException) as exc:
            await cli.download_cli()

    assert exc.value.status_code == 404
    assert exc.value.detail == "CLI package not found"


@pytest.mark.asyncio
async def test_download_sdk_wraps_built_tarball() -> None:
    with (
        patch("shared.version.get_version", return_value="2.0.0"),
        patch("src.services.sdk_package.build_sdk_tarball", return_value=b"tgz") as build,
    ):
        response = await cli.download_sdk()

    assert response.body == b"tgz"
    assert response.media_type == "application/gzip"
    assert response.headers["content-disposition"] == (
        "attachment; filename=bifrost-sdk-2.0.0.tgz"
    )
    build.assert_called_once_with("2.0.0")
