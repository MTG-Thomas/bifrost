from __future__ import annotations

from collections.abc import Iterable
import os
import sys
from pathlib import Path

from fuzz.harnesses import run_harness

TARGETS = {
    "cron_parser_fuzzer": "cron-parser",
    "editor_search_fuzzer": "editor-search",
    "webhook_request_fuzzer": "webhook-request",
}


def _allowed_roots() -> list[Path]:
    roots = [Path.cwd(), Path(__file__).resolve().parents[2]]
    roots.extend(Path(value) for name in ("OUT", "SRC", "WORK") if (value := os.environ.get(name)))
    return [root.resolve() for root in roots if root.exists()]


def _is_under(path: Path, roots: Iterable[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _resolve_input_path(arg: str, roots: Iterable[Path]) -> Path:
    path = Path(arg).resolve(strict=True)
    if not _is_under(path, roots):
        root_list = ", ".join(str(root) for root in roots)
        raise ValueError(f"Refusing to read fuzz input outside allowed roots: {root_list}")
    return path


def _iter_inputs(args: list[str]) -> list[bytes]:
    payloads: list[bytes] = []
    roots = _allowed_roots()
    for arg in args:
        if arg.startswith("-"):
            continue
        path = _resolve_input_path(arg, roots)
        if path.is_dir():
            for child in sorted(path.iterdir()):
                child_path = _resolve_input_path(str(child), roots)
                if child_path.is_file():
                    payloads.append(child_path.read_bytes())
        elif path.is_file():
            payloads.append(path.read_bytes())
    return payloads or [b""]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        raise SystemExit("Usage: cfl_entrypoint.py <fuzzer_name> [corpus paths...]")

    try:
        target = TARGETS[argv[1]]
    except KeyError as exc:
        names = ", ".join(sorted(TARGETS))
        raise SystemExit(
            f"Unknown fuzzer '{argv[1]}'. Known fuzzers: {names}"
        ) from exc

    for payload in _iter_inputs(argv[2:]):
        run_harness(target, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
