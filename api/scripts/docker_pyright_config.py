"""Generate the Pyright config used inside the API quality container."""

from __future__ import annotations

import json
import sys
from typing import Any


def docker_pyright_config(config: dict[str, Any]) -> dict[str, Any]:
    """Translate repository-relative config paths to the /app mount layout."""
    transformed = dict(config)
    transformed.pop("venvPath", None)
    transformed.pop("venv", None)
    transformed["include"] = [
        path.removeprefix("../")
        if path.startswith("../doc_renderer_service/")
        else path
        for path in transformed["include"]
    ]
    return transformed


def main() -> None:
    config = json.load(sys.stdin)
    json.dump(docker_pyright_config(config), sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
