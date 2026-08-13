"""Generate the Pyright config used inside the API quality container."""

from __future__ import annotations

import json
from pathlib import Path
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
    source = Path("pyrightconfig.json")
    destination = Path("pyrightconfig.docker.json")
    config = json.loads(source.read_text())
    destination.write_text(
        json.dumps(docker_pyright_config(config), indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
