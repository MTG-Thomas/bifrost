"""Regression tests for the checked-in Dockerized Pyright config."""

from __future__ import annotations

import json
from pathlib import Path


API_ROOT = Path(__file__).parents[3]


def test_docker_pyright_config_is_the_fixed_container_translation() -> None:
    host_config = json.loads((API_ROOT / "pyrightconfig.json").read_text())
    docker_config = json.loads(
        (API_ROOT / "pyrightconfig.docker.json").read_text()
    )

    expected = dict(host_config)
    expected.pop("venvPath")
    expected.pop("venv")
    expected["include"] = [
        path.removeprefix("../")
        if path.startswith("../doc_renderer_service/")
        else path
        for path in expected["include"]
    ]

    assert docker_config == expected
