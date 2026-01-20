"""
App file dependency parser.

Parses app source code to extract references to workflows.
Used by:
- App code file CRUD operations (app_code_files router)
- GitHub sync app file indexer

Patterns detected:
- useWorkflow('uuid')
- useWorkflow("uuid")
"""

import re
from uuid import UUID

# Regex pattern for extracting workflow dependencies
# Captures UUIDs from hook calls like useWorkflow('550e8400-e29b-41d4-a716-446655440000')
DEPENDENCY_PATTERNS: dict[str, re.Pattern[str]] = {
    "workflow": re.compile(r'useWorkflow\([\'"]([a-f0-9-]{36})[\'"]\)', re.IGNORECASE),
}


def parse_dependencies(source: str) -> list[tuple[str, UUID]]:
    """
    Parse source code and extract workflow dependencies.

    Scans for patterns like useWorkflow('uuid').
    Returns a list of (dependency_type, dependency_id) tuples.

    Args:
        source: The source code to parse

    Returns:
        List of (type, uuid) tuples. Types are: "workflow"
    """
    dependencies: list[tuple[str, UUID]] = []
    seen: set[tuple[str, str]] = set()  # Deduplicate within same file

    for dep_type, pattern in DEPENDENCY_PATTERNS.items():
        for match in pattern.finditer(source):
            uuid_str = match.group(1)
            key = (dep_type, uuid_str)

            if key not in seen:
                seen.add(key)
                try:
                    dependencies.append((dep_type, UUID(uuid_str)))
                except ValueError:
                    # Invalid UUID format, skip
                    pass

    return dependencies
