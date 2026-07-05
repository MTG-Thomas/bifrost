from __future__ import annotations

import sys

import atheris

with atheris.instrument_imports():
    from fuzz.harnesses import fuzz_cron_parser


def TestOneInput(data: bytes) -> None:
    fuzz_cron_parser(data)


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
