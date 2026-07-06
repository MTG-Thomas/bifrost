import pytest
from src.services import app_compiler
from src.services.app_compiler import AppCompilerService, AppTailwindService


@pytest.fixture
def compiler():
    return AppCompilerService()


class TestAppCompilerService:
    @pytest.mark.asyncio
    async def test_compile_batch_empty_returns_empty(self, compiler):
        assert await compiler.compile_batch([]) == []

    @pytest.mark.asyncio
    async def test_compile_simple_component(self, compiler):
        source = 'export default function Page() { return <div>Hello</div>; }'
        result = await compiler.compile_file(source, "pages/index.tsx")
        assert result.success is True
        assert result.compiled is not None
        assert "__defaultExport__" in result.compiled
        assert result.error is None

    @pytest.mark.asyncio
    async def test_compile_with_bifrost_imports(self, compiler):
        source = '''import { Button, useState } from "bifrost";
export default function Page() {
  const [count, setCount] = useState(0);
  return <Button onClick={() => setCount(count + 1)}>{count}</Button>;
}'''
        result = await compiler.compile_file(source, "pages/index.tsx")
        assert result.success is True
        assert "var {" in result.compiled or "var { " in result.compiled

    @pytest.mark.asyncio
    async def test_compile_syntax_error(self, compiler):
        source = 'export default function Page() { return <div>; }'
        result = await compiler.compile_file(source, "pages/index.tsx")
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_compile_batch(self, compiler):
        files = [
            {"path": "pages/index.tsx", "source": "export default function A() { return <div>A</div>; }"},
            {"path": "pages/about.tsx", "source": "export default function B() { return <div>B</div>; }"},
        ]
        results = await compiler.compile_batch(files)
        assert len(results) == 2
        assert all(r.success for r in results)

    @pytest.mark.asyncio
    async def test_compile_batch_partial_failure(self, compiler):
        files = [
            {"path": "pages/good.tsx", "source": "export default function A() { return <div>A</div>; }"},
            {"path": "pages/bad.tsx", "source": "export default function B() { return <div>; }"},
        ]
        results = await compiler.compile_batch(files)
        assert results[0].success is True
        assert results[1].success is False

    @pytest.mark.asyncio
    async def test_compile_batch_reports_subprocess_exit_error(self, compiler, monkeypatch):
        async def create_subprocess_exec(*_args, **_kwargs):
            return _FakeProcess(
                returncode=1,
                stdout=b"",
                stderr=b"babel unavailable",
            )

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        results = await compiler.compile_batch([
            {"path": "pages/a.tsx", "source": "export default 1"},
            {"path": "pages/b.tsx", "source": "export default 2"},
        ])

        assert [result.path for result in results] == ["pages/a.tsx", "pages/b.tsx"]
        assert all(result.success is False for result in results)
        assert {result.error for result in results} == {"babel unavailable"}

    @pytest.mark.asyncio
    async def test_compile_batch_reports_protocol_error(self, compiler, monkeypatch):
        async def create_subprocess_exec(*_args, **_kwargs):
            return _FakeProcess(
                returncode=0,
                stdout=b'{"error": "invalid compiler response"}',
                stderr=b"",
            )

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        results = await compiler.compile_batch([
            {"path": "pages/a.tsx", "source": "export default 1"},
        ])

        assert results[0].success is False
        assert results[0].error == "invalid compiler response"

    @pytest.mark.asyncio
    async def test_compile_batch_handles_missing_node(self, compiler, monkeypatch):
        async def create_subprocess_exec(*_args, **_kwargs):
            raise FileNotFoundError("node")

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            create_subprocess_exec,
        )

        result = await compiler.compile_file("export default 1", "pages/a.tsx")

        assert result.success is False
        assert result.error == "Node.js not available"


class TestAppTailwindService:
    def test_extract_candidates_keeps_tailwind_arbitrary_values(self):
        candidates = AppTailwindService.extract_candidates([
            'const c = "flex lg:grid-cols-[minmax(0,1fr)_360px] bg-[rgb(0,0,0)]";',
            'const ignored = "123 !!!";',
        ])

        assert "flex" in candidates
        assert "lg:grid-cols-[minmax(0,1fr)_360px]" in candidates
        assert "bg-[rgb(0,0,0)]" in candidates
        assert "123" not in candidates

    @pytest.mark.asyncio
    async def test_generate_css_skips_empty_candidates(self, monkeypatch):
        async def fail_invoke(_payload):
            raise AssertionError("should not invoke tailwind without candidates")

        monkeypatch.setattr(AppTailwindService, "_invoke", fail_invoke)

        assert await AppTailwindService.generate_css(['const label = "123 !!!";']) is None

    @pytest.mark.asyncio
    async def test_generate_css_pipeline_invokes_for_user_css_without_candidates(
        self, monkeypatch
    ):
        calls = []

        async def fake_invoke(payload):
            calls.append(payload)
            return "/* css */"

        monkeypatch.setattr(AppTailwindService, "_invoke", fake_invoke)

        assert await AppTailwindService.generate_css_pipeline(
            ['const label = "123 !!!";'],
            [("theme.css", "@theme { --color-brand: red; }")],
        ) == "/* css */"
        assert calls == [
            {
                "candidates": [],
                "user_css": [
                    {"path": "theme.css", "content": "@theme { --color-brand: red; }"}
                ],
            }
        ]

    @pytest.mark.asyncio
    async def test_tailwind_invoke_handles_process_and_payload_errors(self, monkeypatch):
        async def nonzero_subprocess(*_args, **_kwargs):
            return _FakeProcess(returncode=1, stdout=b"", stderr=b"tailwind failed")

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            nonzero_subprocess,
        )
        assert await AppTailwindService._invoke({"candidates": ["flex"]}) is None

        async def error_payload_subprocess(*_args, **_kwargs):
            return _FakeProcess(
                returncode=0,
                stdout=b'{"error": "bad css"}',
                stderr=b"",
            )

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            error_payload_subprocess,
        )
        assert await AppTailwindService._invoke({"candidates": ["grid"]}) is None

    @pytest.mark.asyncio
    async def test_tailwind_invoke_returns_css_or_none(self, monkeypatch):
        async def css_subprocess(*_args, **_kwargs):
            return _FakeProcess(returncode=0, stdout=b'{"css": ".flex{}"}', stderr=b"")

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            css_subprocess,
        )
        assert await AppTailwindService._invoke({"candidates": ["flex"]}) == ".flex{}"

        async def empty_css_subprocess(*_args, **_kwargs):
            return _FakeProcess(returncode=0, stdout=b'{"css": ""}', stderr=b"")

        monkeypatch.setattr(
            app_compiler.asyncio,
            "create_subprocess_exec",
            empty_css_subprocess,
        )
        assert await AppTailwindService._invoke({"candidates": ["hidden"]}) is None


class _FakeProcess:
    def __init__(self, *, returncode: int, stdout: bytes, stderr: bytes):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.input = None

    async def communicate(self, input=None):
        self.input = input
        return self._stdout, self._stderr
