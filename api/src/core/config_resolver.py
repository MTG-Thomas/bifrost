"""Compatibility import target for legacy config resolver tests.

Configuration reads now go through ``src.repositories.config.ConfigRepository``.
This class remains as a narrow patch target for tests that assert authorization
fails before any secret-resolution object is constructed.
"""


class ConfigResolver:
    """Legacy resolver placeholder; do not use for new config reads."""

    pass
