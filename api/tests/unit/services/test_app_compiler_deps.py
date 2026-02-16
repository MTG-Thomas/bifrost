"""Tests for external dependency import transforms in the app compiler."""
import pytest
from src.services.app_compiler import AppCompilerService


@pytest.fixture
def compiler():
    return AppCompilerService()


@pytest.mark.asyncio
async def test_named_import_transforms_to_deps(compiler):
    """import { X, Y } from "recharts" → const { X, Y } = $deps["recharts"];"""
    source = '''
import { LineChart, Line } from "recharts";
export default function Chart() {
    return <LineChart><Line dataKey="value" /></LineChart>;
}
'''
    result = await compiler.compile_file(source, "pages/chart.tsx")
    assert result.success
    assert '$deps["recharts"]' in result.compiled
    assert "import " not in result.compiled  # no raw imports left


@pytest.mark.asyncio
async def test_default_import_transforms_to_deps(compiler):
    """import X from "dayjs" → const X = ($deps["dayjs"].default || $deps["dayjs"]);"""
    source = '''
import dayjs from "dayjs";
export default function Page() {
    return <div>{dayjs().format("MMM D")}</div>;
}
'''
    result = await compiler.compile_file(source, "pages/index.tsx")
    assert result.success
    assert '$deps["dayjs"]' in result.compiled


@pytest.mark.asyncio
async def test_namespace_import_transforms_to_deps(compiler):
    """import * as R from "recharts" → const R = $deps["recharts"];"""
    source = '''
import * as R from "recharts";
export default function Chart() {
    return <R.LineChart><R.Line /></R.LineChart>;
}
'''
    result = await compiler.compile_file(source, "pages/chart.tsx")
    assert result.success
    assert '$deps["recharts"]' in result.compiled


@pytest.mark.asyncio
async def test_mixed_import_transforms_to_deps(compiler):
    """import X, { Y } from "pkg" → default + named destructuring."""
    source = '''
import Pkg, { Helper } from "some-pkg";
export default function Page() {
    return <div><Pkg /><Helper /></div>;
}
'''
    result = await compiler.compile_file(source, "pages/index.tsx")
    assert result.success
    assert '$deps["some-pkg"]' in result.compiled


@pytest.mark.asyncio
async def test_bifrost_imports_unchanged(compiler):
    """Bifrost imports should still use $ scope, not $deps."""
    source = '''
import { Button, Card } from "bifrost";
export default function Page() {
    return <Card><Button>Click</Button></Card>;
}
'''
    result = await compiler.compile_file(source, "pages/index.tsx")
    assert result.success
    assert "const {" in result.compiled
    assert "= $;" in result.compiled or "= $" in result.compiled
    assert "$deps" not in result.compiled


@pytest.mark.asyncio
async def test_mixed_bifrost_and_external_imports(compiler):
    """Bifrost and external imports coexist."""
    source = '''
import { Card, useWorkflowQuery } from "bifrost";
import { LineChart, Line } from "recharts";
import dayjs from "dayjs";

export default function Dashboard() {
    const { data } = useWorkflowQuery("get_metrics");
    return (
        <Card>
            <p>{dayjs().format("MMM D")}</p>
            <LineChart data={data}><Line dataKey="value" /></LineChart>
        </Card>
    );
}
'''
    result = await compiler.compile_file(source, "pages/dashboard.tsx")
    assert result.success
    assert "= $;" in result.compiled  # bifrost imports
    assert '$deps["recharts"]' in result.compiled
    assert '$deps["dayjs"]' in result.compiled


@pytest.mark.asyncio
async def test_no_imports_compiles_normally(compiler):
    """Files with no imports should compile without $deps references."""
    source = '''
export default function Page() {
    return <div>Hello</div>;
}
'''
    result = await compiler.compile_file(source, "pages/index.tsx")
    assert result.success
    assert "$deps" not in result.compiled
