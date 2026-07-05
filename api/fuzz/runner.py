from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from fuzz.harnesses import run_harness


CORPUS_DIR = Path(__file__).resolve().parent / "corpora"


@dataclass(frozen=True)
class CorpusCase:
    target: str
    path: Path
    data: bytes

    @property
    def id(self) -> str:
        return str(Path(self.target) / self.path.name)


def iter_corpus_cases(corpus_dir: Path = CORPUS_DIR) -> list[CorpusCase]:
    cases: list[CorpusCase] = []
    for target_dir in sorted(path for path in corpus_dir.iterdir() if path.is_dir()):
        for path in sorted(target_dir.iterdir()):
            if path.is_file():
                cases.append(
                    CorpusCase(
                        target=target_dir.name,
                        path=path,
                        data=path.read_bytes(),
                    )
                )
    return cases


def run_corpus(corpus_dir: Path = CORPUS_DIR) -> int:
    cases = iter_corpus_cases(corpus_dir)
    for case in cases:
        run_harness(case.target, case.data)
    return len(cases)


def main() -> None:
    logging.disable(logging.WARNING)
    count = run_corpus()
    print(f"Ran {count} fuzz corpus cases.")


if __name__ == "__main__":
    main()
