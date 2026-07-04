"""Unit tests for the Solution zip-install PREVIEW path (parse-only) + zip-slip
safety. The preview function unzips a Solution workspace, parses the manifests
via the CLI collectors, and returns what it would create — no DB, no S3, no
build. The COMMIT path is covered by the e2e test (it needs a live deployer)."""
from __future__ import annotations

import io
import logging
import zipfile

import pytest

from src.models.enums import ConfigType
from src.services.solutions.zip_install import (
    ContentCollision,
    PreviewResult,
    _config_type,
    _safe_extract_path,
    preview_zip,
)


def _make_workspace_zip(extra: dict[str, str] | None = None) -> bytes:
    """Build an in-memory Solution workspace zip with a descriptor, a workflow
    manifest + source, and a required-secret config declaration."""
    files: dict[str, str] = {
        "bifrost.solution.yaml": (
            "slug: zip-demo\nname: Zip Demo\nscope: global\n"
        ),
        ".bifrost/workflows.yaml": (
            "workflows:\n"
            "  11111111-1111-1111-1111-111111111111:\n"
            "    id: 11111111-1111-1111-1111-111111111111\n"
            "    name: main\n"
            "    function_name: run\n"
            "    path: workflows/main.py\n"
        ),
        ".bifrost/configs.yaml": (
            "configs:\n"
            "  API_KEY:\n"
            "    id: API_KEY\n"
            "    key: API_KEY\n"
            "    type: secret\n"
            "    required: true\n"
            "    description: needed\n"
            "    position: 0\n"
        ),
        ".bifrost/claims.yaml": (
            "claims:\n"
            "  22222222-2222-2222-2222-222222222222:\n"
            "    id: 22222222-2222-2222-2222-222222222222\n"
            "    name: allowed_campus_ids\n"
            "    type: list\n"
            "    query:\n"
            "      table: memberships\n"
            "      select: campus_id\n"
        ),
        "workflows/main.py": "def run(sdk):\n    return 'ok'\n",
    }
    if extra:
        files.update(extra)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def test_preview_lists_entities_and_config_schemas() -> None:
    result = preview_zip(_make_workspace_zip())
    assert isinstance(result, PreviewResult)
    assert result.slug == "zip-demo"
    assert result.name == "Zip Demo"

    assert len(result.workflows) == 1
    assert result.workflows[0]["name"] == "main"
    assert result.workflows[0]["function_name"] == "run"

    assert len(result.config_schemas) == 1
    decl = result.config_schemas[0]
    assert decl["key"] == "API_KEY"
    assert decl["type"] == "secret"
    assert decl["required"] is True

    assert len(result.claims) == 1
    assert result.claims[0]["name"] == "allowed_campus_ids"


def test_preview_empty_collections_when_absent() -> None:
    """A descriptor-only workspace previews with empty entity lists, not an error."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bifrost.solution.yaml", "slug: bare\nname: Bare\nscope: global\n")
    result = preview_zip(buf.getvalue())
    assert result.slug == "bare"
    assert result.workflows == []
    assert result.config_schemas == []
    assert result.apps == []


def test_zip_slip_member_is_rejected() -> None:
    """A member whose resolved path escapes the temp root must raise ValueError."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("bifrost.solution.yaml", "slug: evil\nname: Evil\nscope: global\n")
        z.writestr("../evil.txt", "pwned")
    with pytest.raises(ValueError, match="unsafe path"):
        preview_zip(buf.getvalue())


def test_bad_zip_bytes_raise() -> None:
    """Non-zip bytes raise BadZipFile (the endpoint maps it to a 422)."""
    with pytest.raises(zipfile.BadZipFile):
        preview_zip(b"this is not a zip file")


def test_preview_requires_password_false_for_normal_zip() -> None:
    """A regular (shareable) zip without .bifrost/secrets.enc reports requires_password=False."""
    result = preview_zip(_make_workspace_zip())
    assert result.requires_password is False


def test_preview_requires_password_true_for_full_backup_zip() -> None:
    """A full-backup zip carrying .bifrost/secrets.enc reports requires_password=True."""
    result = preview_zip(
        _make_workspace_zip(extra={".bifrost/secrets.enc": "encrypted-blob-placeholder"})
    )
    assert result.requires_password is True


def test_safe_extract_path_rejects_zip_slip_member(tmp_path) -> None:
    zip_path = tmp_path / "evil.zip"
    dest = tmp_path / "dest"
    dest.mkdir()
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("bifrost.solution.yaml", "slug: ok\nname: OK\n")
        z.writestr("../evil.txt", "pwned")

    with pytest.raises(ValueError, match="unsafe path"):
        _safe_extract_path(zip_path, str(dest))

    assert not (tmp_path / "evil.txt").exists()


def test_safe_extract_path_extracts_normal_zip(tmp_path) -> None:
    zip_path = tmp_path / "ok.zip"
    dest = tmp_path / "dest"
    dest.mkdir()
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("nested/file.txt", "ok")

    _safe_extract_path(zip_path, str(dest))

    assert (dest / "nested" / "file.txt").read_text() == "ok"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ConfigType.STRING),
        ("", ConfigType.STRING),
        ("string", ConfigType.STRING),
        ("SECRET", ConfigType.SECRET),
    ],
)
def test_config_type_maps_known_values(raw: str | None, expected: ConfigType) -> None:
    assert _config_type(raw, key="API_KEY") is expected


def test_config_type_logs_and_defaults_unknown_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="src.services.solutions.zip_install"):
        resolved = _config_type("sekret", key="API_KEY")

    assert resolved is ConfigType.STRING
    assert "unrecognized type" in caplog.text
    assert "API_KEY" in caplog.text


def test_content_collision_message_lists_sorted_keys_and_tables() -> None:
    collision = ContentCollision(keys=["Z_KEY", "A_KEY"], tables=["tickets"])

    assert collision.keys == ["Z_KEY", "A_KEY"]
    assert collision.tables == ["tickets"]
    assert str(collision) == (
        "Import would overwrite existing config values: A_KEY, Z_KEY; "
        "table data: tickets. Re-run with replace to overwrite."
    )
