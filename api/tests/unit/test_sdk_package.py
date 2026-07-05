"""/api/sdk/download builds an installable `bifrost` package (Codex P1-a).

A standalone_v2 app resolves `import { BifrostProvider, useWorkflow } from
"bifrost"` from the instance. This test exercises the real esbuild bundle of the
SDK source and asserts the produced npm tarball has the right shape: a
`package/package.json` named `bifrost` with React peer deps + a `dist/index.mjs`
bundle that exports the v2 surface and keeps React external.

The SDK source is copied into `sdk_package/sdk_src/` in the api image (Dockerfile).
When running before that image layer exists (host/older container), this test
stages the same files from the client tree so it still validates the bundler.
"""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

_SDK_SERVICE = Path("/app/src/services/sdk_package")


def _ensure_sdk_src() -> bool:
    """Return whether the baked SDK source or client fallback tree is available."""
    dst = _SDK_SERVICE / "sdk_src"
    if (dst / "index.ts").is_file():
        return True
    candidates = [
        Path("/client/src/lib/app-sdk"),
        Path(__file__).resolve().parents[3] / "client" / "src" / "lib" / "app-sdk",
    ]
    return any((c / "index.v2.ts").is_file() for c in candidates)


def test_pep440ish_coerces_git_describe_versions():
    import src.services.sdk_package as sdkpkg

    assert sdkpkg._pep440ish("v1.2-3-gabc1234") == "1.2.0"
    assert sdkpkg._pep440ish("2.3.4-dirty") == "2.3.4"
    assert sdkpkg._pep440ish("unknown") == "0.0.0"


def test_materialize_sdk_src_prefers_baked_source(tmp_path, monkeypatch):
    import src.services.sdk_package as sdkpkg

    baked = tmp_path / "baked"
    baked.mkdir()
    (baked / "index.ts").write_text("export const baked = true")
    monkeypatch.setattr(sdkpkg, "_SDK_SRC", baked)

    assert sdkpkg._materialize_sdk_src(tmp_path / "work") == baked


def test_materialize_sdk_src_stages_client_fallback(tmp_path, monkeypatch):
    import src.services.sdk_package as sdkpkg

    client_src = tmp_path / "api" / "client" / "src" / "lib" / "app-sdk"
    client_src.mkdir(parents=True)
    for name in sdkpkg._SDK_SOURCE_FILES:
        (client_src / name).write_text(f"// {name}")
    (client_src / "index.v2.ts").write_text("export const v2 = true")

    monkeypatch.setattr(sdkpkg, "_client_sdk_candidates", lambda: [client_src])
    monkeypatch.setattr(sdkpkg, "_SDK_SRC", tmp_path / "missing")

    staged = sdkpkg._materialize_sdk_src(tmp_path / "work")

    assert staged == tmp_path / "work" / "sdk_src"
    assert (staged / "provider.tsx").read_text() == "// provider.tsx"
    assert (staged / "index.ts").read_text() == "export const v2 = true"


def test_materialize_sdk_src_returns_baked_path_when_no_source_available(
    tmp_path, monkeypatch
):
    import src.services.sdk_package as sdkpkg

    missing = tmp_path / "missing"

    monkeypatch.setattr(sdkpkg, "_client_sdk_candidates", lambda: [])
    monkeypatch.setattr(sdkpkg, "_SDK_SRC", missing)

    assert sdkpkg._materialize_sdk_src(tmp_path / "work") == missing


def test_bundle_invokes_node_builder_with_materialized_source(tmp_path, monkeypatch):
    import src.services.sdk_package as sdkpkg

    src = tmp_path / "sdk_src"
    src.mkdir()
    calls = []

    def fake_materialize(workdir):
        assert workdir == tmp_path
        return src

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        Path(argv[3]).write_bytes(b"// bundled sdk")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(sdkpkg, "_materialize_sdk_src", fake_materialize)
    monkeypatch.setattr(sdkpkg.subprocess, "run", fake_run)

    assert sdkpkg._bundle(tmp_path) == b"// bundled sdk"
    argv, kwargs = calls[0]
    assert argv[:2] == ["node", str(sdkpkg._BUILDER)]
    assert argv[2] == str(src)
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["check"] is True
    assert kwargs["timeout"] == 120
    assert kwargs["env"]["NODE_PATH"] == str(sdkpkg._NODE_MODULES)


@pytest.mark.e2e
def test_build_sdk_tarball_cached_per_version(monkeypatch):
    """The tarball is a pure function of version + baked-in SDK source, and
    /api/sdk/download + every app deploy call it — so the esbuild subprocess
    must run ONCE per version, not per request."""
    import src.services.sdk_package as sdkpkg

    sdkpkg.build_sdk_tarball.cache_clear()
    calls: list[Path] = []

    def _fake_bundle(workdir: Path) -> bytes:
        calls.append(workdir)
        return b"//bundle"

    monkeypatch.setattr(sdkpkg, "_bundle", _fake_bundle)
    try:
        first = sdkpkg.build_sdk_tarball("v9.9.9")
        second = sdkpkg.build_sdk_tarball("v9.9.9")
    finally:
        # Don't leak the fake-bundle tarball into other tests via the cache.
        sdkpkg.build_sdk_tarball.cache_clear()

    assert first == second
    assert len(calls) == 1, "builder ran more than once for the same version"


@pytest.mark.e2e
def test_build_sdk_tarball_shape_and_exports():
    if not _ensure_sdk_src():
        pytest.skip("SDK source not available (no image copy, no client tree)")
    # esbuild must be installed (app_bundler node_modules) — skip if not present.
    if not (_SDK_SERVICE.parent / "app_bundler" / "node_modules" / "esbuild").exists():
        pytest.skip("esbuild not installed in this environment")

    import src.services.sdk_package as sdkpkg

    sdkpkg.build_sdk_tarball.cache_clear()  # never serve another test's stubbed bundle
    data = sdkpkg.build_sdk_tarball("v1.2-3-gabc1234")
    assert data[:2] == b"\x1f\x8b", "not a gzip tarball"

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        names = tar.getnames()
        assert "package/package.json" in names
        assert "package/dist/index.mjs" in names

        pkg_file = tar.extractfile("package/package.json")
        assert pkg_file is not None
        pkg = json.loads(pkg_file.read())
        assert pkg["name"] == "bifrost"
        # git-describe "v1.2-3-gabc1234" has no patch component → coerced to 1.2.0.
        assert pkg["version"] == "1.2.0"
        # No runtime deps — the SDK is fetch + React + lucide (all peers) only.
        assert "dependencies" not in pkg or not pkg["dependencies"]
        assert "react" in pkg["peerDependencies"]
        assert "lucide-react" in pkg["peerDependencies"]

        bundle_file = tar.extractfile("package/dist/index.mjs")
        assert bundle_file is not None
        bundle = bundle_file.read().decode()
        for sym in (
            "BifrostProvider", "useWorkflow", "useWorkflowQuery",
            "useWorkflowMutation", "useTable", "tables", "BifrostHeader",
        ):
            assert sym in bundle, f"{sym} missing from bundle"
        # React + lucide stay external (imported, not inlined).
        assert 'from "react"' in bundle
        assert 'from "lucide-react"' in bundle
