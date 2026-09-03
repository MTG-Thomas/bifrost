import os
import subprocess
from functools import lru_cache


# Frozen bridge for CLIs that shipped with minimum-version gating. New CLIs use
# contract_version; do not advance this beyond an actually published CLI build.
LEGACY_MIN_CLI_VERSION = "1.1.1-dev.548"


@lru_cache(maxsize=1)
def get_version() -> str:
    if v := os.environ.get("BIFROST_VERSION"):
        return v
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"
