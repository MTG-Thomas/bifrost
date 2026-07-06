from __future__ import annotations

import sys

import atheris

with atheris.instrument_imports():
    from fuzz.harnesses import fuzz_editor_search


def TestOneInput(data: bytes) -> None:
    fuzz_editor_search(data)


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
