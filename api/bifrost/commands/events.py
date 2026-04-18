"""Placeholder for bifrost events CLI commands.

Commands in this module are added by Tasks 5a–5j of the CLI mutation surface
plan. The empty group is registered here so :mod:`bifrost.commands.__init__`
can import it while other subgroups are still being filled in.
"""

from __future__ import annotations

from .base import entity_group

events_group = entity_group("events", "events commands (scaffolding — see plan tasks 5a–5j).")

__all__ = ["events_group"]
