import json
import subprocess
import sys
from pathlib import Path


def test_app_startup_does_not_force_standalone_decorator_fallback() -> None:
    api_root = Path(__file__).resolve().parents[3]
    code = """
import json
from src.main import app
import bifrost

@bifrost.workflow()
async def example(name: str, count: int = 1) -> dict:
    return {"name": name, "count": count}

print(json.dumps({
    "decorator_module": bifrost.workflow.__module__,
    "parameters": [parameter.name for parameter in example._executable_metadata.parameters],
}))
"""

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=api_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "decorator_module": "src.sdk.decorators",
        "parameters": ["name", "count"],
    }
