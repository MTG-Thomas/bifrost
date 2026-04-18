"""Placeholder for bifrost roles CLI commands.

Commands in this module are added by Tasks 5a–5j of the CLI mutation surface
plan. The empty group is registered here so :mod:`bifrost.commands.__init__`
can import it while other subgroups are still being filled in.
"""

from __future__ import annotations

from .base import entity_group

roles_group = entity_group("roles", "roles commands (scaffolding — see plan tasks 5a–5j).")

__all__ = ["roles_group"]
