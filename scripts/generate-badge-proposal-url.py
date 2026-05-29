#!/usr/bin/env python3
"""Build OpenSSF BadgeApp automation-proposal URLs from .bestpractices.json.

Official docs:
https://github.com/coreinfrastructure/best-practices-badge/blob/main/docs/automation-proposals.md

Example (baseline OSPS, section chooser):
  python scripts/generate-badge-proposal-url.py --section choose \\
    --fields osps_ac_01_01_status,osps_ac_01_01_justification \\
    --values Met,"GitHub org enforces 2FA"

Example (all Met/N/A fields from repo JSON for Silver):
  python scripts/generate-badge-proposal-url.py --section silver --from-json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

BASE_URL = "https://www.bestpractices.dev"
PROJECT_ID = 13022
LOCALE = "en"
ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / ".bestpractices.json"

VALID_SECTIONS = frozenset(
    {"choose", "passing", "silver", "gold", "baseline-1", "baseline-2", "baseline-3"}
)


def parse_field_values(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Expected KEY=VALUE, got {pair!r}")
        key, value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Empty key in {pair!r}")
        out[key] = value
    return out


def fields_from_json(data: dict[str, str]) -> dict[str, str]:
    """Include status/justification pairs where status is Met or N/A."""
    out: dict[str, str] = {}
    for key, value in data.items():
        if key.endswith("_status") and value in {"Met", "N/A"}:
            out[key] = value
            justification_key = key.removesuffix("_status") + "_justification"
            justification = data.get(justification_key)
            if justification:
                out[justification_key] = justification
    return out


def build_url(
    section: str,
    params: dict[str, str],
    *,
    overrides: str | None = None,
) -> str:
    if section not in VALID_SECTIONS:
        raise ValueError(f"section must be one of: {', '.join(sorted(VALID_SECTIONS))}")
    query: dict[str, str] = dict(params)
    if overrides:
        query["overrides"] = overrides
    query_string = urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    return f"{BASE_URL}/{LOCALE}/projects/{PROJECT_ID}/{section}/edit?{query_string}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate BadgeApp automation-proposal edit URLs."
    )
    parser.add_argument(
        "--section",
        default="choose",
        help="Badge form section (choose, passing, silver, baseline-1, ...)",
    )
    parser.add_argument(
        "--from-json",
        action="store_true",
        help=f"Load Met/N/A fields from {DATA_FILE.name}",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Proposal field (repeatable). Example: osps_ac_01_01_status=Met",
    )
    parser.add_argument(
        "--overrides",
        help="Comma-separated globs to force proposals (e.g. osps_ac_*,governance_*)",
    )
    parser.add_argument(
        "--prefix",
        action="append",
        default=[],
        metavar="PREFIX",
        help="With --from-json, only include keys starting with PREFIX (repeatable)",
    )
    args = parser.parse_args()

    params: dict[str, str] = {}
    if args.from_json:
        if not DATA_FILE.exists():
            print(f"Missing {DATA_FILE}. Run generate-bestpractices-json.py first.", file=sys.stderr)
            return 1
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        params = fields_from_json(data)
        if args.prefix:
            prefixes = tuple(args.prefix)
            params = {k: v for k, v in params.items() if k.startswith(prefixes)}
    params.update(parse_field_values(args.field))

    if not params:
        print("No proposal fields. Use --from-json and/or --field KEY=VALUE.", file=sys.stderr)
        return 1

    url = build_url(args.section, params, overrides=args.overrides)
    print(url)
    print(
        f"\nOpen while signed in to BadgeApp. Review highlighted proposals, then Save.",
        file=sys.stderr,
    )
    print(
        f"Fields: {len(params)} | Section: {args.section} | Project: {PROJECT_ID}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
