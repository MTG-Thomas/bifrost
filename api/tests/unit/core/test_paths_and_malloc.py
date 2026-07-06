from pathlib import Path

from src.core import malloc, paths


def test_create_ephemeral_temp_dir_uses_prefix_and_creates_directory():
    temp_dir = paths.create_ephemeral_temp_dir(prefix="bifrost-test-")

    try:
        assert temp_dir.exists()
        assert temp_dir.is_dir()
        assert temp_dir.name.startswith("bifrost-test-")
    finally:
        temp_dir.rmdir()


def test_create_session_temp_dir_uses_given_or_generated_session(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "Path", lambda value: tmp_path / Path(value).name)

    explicit = paths.create_session_temp_dir("session-1")
    generated = paths.create_session_temp_dir()

    assert explicit == tmp_path / "session-1"
    assert explicit.is_dir()
    assert generated.parent == tmp_path
    assert generated.is_dir()


def test_trim_malloc_loads_libc_once_and_calls_malloc_trim(monkeypatch):
    calls = []

    class FakeLibc:
        def malloc_trim(self, value):
            calls.append(value)

    loaded = []

    def fake_cdll(name):
        loaded.append(name)
        return FakeLibc()

    monkeypatch.setattr(malloc, "_libc", None)
    monkeypatch.setattr(malloc.ctypes, "CDLL", fake_cdll)

    malloc.trim_malloc()
    malloc.trim_malloc()

    assert loaded == ["libc.so.6"]
    assert calls == [0, 0]


def test_trim_malloc_tolerates_missing_or_failing_libc(monkeypatch):
    def missing_cdll(_name):
        raise OSError("not glibc")

    monkeypatch.setattr(malloc, "_libc", None)
    monkeypatch.setattr(malloc.ctypes, "CDLL", missing_cdll)
    malloc.trim_malloc()

    class BrokenLibc:
        def malloc_trim(self, _value):
            raise RuntimeError("trim failed")

    monkeypatch.setattr(malloc, "_libc", BrokenLibc())
    malloc.trim_malloc()
