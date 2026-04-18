"""Placeholder for bifrost integrations CLI commands.

Commands in this module are added by Tasks 5a–5j of the CLI mutation surface
plan. The empty group is registered here so :mod:`bifrost.commands.__init__`
can import it while other subgroups are still being filled in.
"""

from __future__ import annotations

from .base import entity_group

integrations_group = entity_group("integrations", "integrations commands (scaffolding — see plan tasks 5a–5j).")

__all__ = ["integrations_group"]
