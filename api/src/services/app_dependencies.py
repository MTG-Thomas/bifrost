"""
App file dependency parser.

Parses app source code to extract references to workflows.

Used by:
- Dependency graph service
- Maintenance scan-app-dependencies endpoint

Patterns detected:
- useWorkflowQuery('name_or_uuid')
- useWorkflowMutation('name_or_uuid')
- useWorkflow('name_or_uuid') (legacy, kept for backward compat)

The parser extracts any string argument from these hooks.
"""

import re

# Regex pattern for extracting workflow references from hook calls.
# Captures any non-empty string argument (name or UUID) from:
# useWorkflowQuery('...'), useWorkflowMutation('...'), useWorkflow('...')
DEPENDENCY_PATTERN = re.compile(
    r"""(?:useWorkflow(?:Query|Mutation)?)\(\s*['"]([^'"]+)['"]\s*\)""",
    re.IGNORECASE,
)


def parse_dependencies(source: str) -> list[str]:
    """
    Parse source code and extract workflow reference strings.

    Scans for patterns like useWorkflowQuery('ref'), useWorkflowMutation('ref'),
    or useWorkflow('ref'). Returns deduplicated list of string references
    (which may be workflow names or UUIDs).

    Args:
        source: The source code to parse

    Returns:
        List of unique reference strings found in hook calls.
    """
    refs: list[str] = []
    seen: set[str] = set()

    for match in DEPENDENCY_PATTERN.finditer(source):
        ref = match.group(1)
        if ref not in seen:
            seen.add(ref)
            refs.append(ref)

    return refs
