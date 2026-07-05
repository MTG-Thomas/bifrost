from __future__ import annotations

import ast
from pathlib import Path

from fuzz.harnesses import HARNESS_TARGETS


ATHERIS_TARGET_DIR = Path(__file__).resolve().parents[3] / "fuzz" / "atheris_targets"
TARGET_TO_FUZZER = {
    "cron-parser": "cron_parser_fuzzer.py",
    "editor-search": "editor_search_fuzzer.py",
    "webhook-request": "webhook_request_fuzzer.py",
}


def test_clusterfuzzlite_targets_cover_registered_harnesses():
    assert set(TARGET_TO_FUZZER) == set(HARNESS_TARGETS)

    for fuzzer_file in TARGET_TO_FUZZER.values():
        assert (ATHERIS_TARGET_DIR / fuzzer_file).is_file()


def test_clusterfuzzlite_targets_expose_atheris_entrypoint():
    for fuzzer_file in TARGET_TO_FUZZER.values():
        module = ast.parse((ATHERIS_TARGET_DIR / fuzzer_file).read_text())
        function_names = {
            node.name for node in module.body if isinstance(node, ast.FunctionDef)
        }

        assert {"TestOneInput", "main"} <= function_names
