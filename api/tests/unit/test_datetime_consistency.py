"""
Static analysis tests to enforce datetime standardization.

These tests scan the codebase to ensure timezone-aware UTC datetime patterns
are used consistently after the migration from naive UTC.
"""
from pathlib import Path


API_SRC_DIR = Path(__file__).parent.parent.parent / "src"
API_MODELS_ORM_DIR = API_SRC_DIR / "models" / "orm"


def get_python_files(directory: Path) -> list[Path]:
    """Get all Python files in a directory recursively."""
    return list(directory.rglob("*.py"))


class TestDatetimeConsistency:
    """Ensure datetime patterns are consistent across the codebase."""

    def test_no_naive_datetime_columns_in_orm(self):
        """ORM models must use DateTime(timezone=True), not bare DateTime()."""
        violations = []

        for py_file in get_python_files(API_MODELS_ORM_DIR):
            content = py_file.read_text()
            for i, line in enumerate(content.split("\n"), 1):
                stripped = line.strip()
                # Skip comments and import lines
                if stripped.startswith("#") or stripped.startswith("from ") or stripped.startswith("import "):
                    continue
                # Look for DateTime usage in column definitions
                if "DateTime" in line and "mapped_column" in line:
                    # Must have timezone=True
                    if "DateTime(timezone=True)" not in line:
                        violations.append(f"{py_file.name}:{i}")

        assert not violations, (
            "Found DateTime without timezone=True in ORM models. "
            "Use DateTime(timezone=True) instead.\nViolations:\n" + "\n".join(violations)
        )

    def test_no_datetime_utcnow(self):
        """Code must not use deprecated datetime.utcnow()."""
        violations = []

        for py_file in get_python_files(API_SRC_DIR):
            content = py_file.read_text()
            if "datetime.utcnow" in content:
                for i, line in enumerate(content.split("\n"), 1):
                    if "datetime.utcnow" in line:
                        stripped = line.strip()
                        if not stripped.startswith("#"):
                            violations.append(f"{py_file.relative_to(API_SRC_DIR)}:{i}")

        assert not violations, (
            "Found datetime.utcnow() in source code. "
            "Use datetime.now(timezone.utc) instead.\nViolations:\n" + "\n".join(violations)
        )

    def test_no_bare_datetime_now(self):
        """Code must not use datetime.now() without timezone (local time)."""
        violations = []

        for py_file in get_python_files(API_SRC_DIR):
            content = py_file.read_text()
            lines = content.split("\n")

            for i, line in enumerate(lines, 1):
                # Match datetime.now() but not datetime.now(timezone.utc)
                if "datetime.now()" in line and "timezone" not in line:
                    # Skip comments
                    stripped = line.strip()
                    if not stripped.startswith("#"):
                        violations.append(f"{py_file.relative_to(API_SRC_DIR)}:{i}")

        assert not violations, (
            "Found datetime.now() (local time) in source code. "
            "Use datetime.now(timezone.utc) instead.\nViolations:\n" + "\n".join(violations)
        )
