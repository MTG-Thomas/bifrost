"""Regression tests for the Dockerized Pyright path translation."""

from scripts.docker_pyright_config import docker_pyright_config


def test_document_renderer_include_targets_the_compose_mount() -> None:
    original = {
        "include": [
            "src/**/*.py",
            "../doc_renderer_service/**/*.py",
        ],
        "venvPath": "..",
        "venv": ".venv",
    }

    transformed = docker_pyright_config(original)

    assert transformed == {
        "include": ["src/**/*.py", "doc_renderer_service/**/*.py"]
    }
    assert original["include"][1] == "../doc_renderer_service/**/*.py"
