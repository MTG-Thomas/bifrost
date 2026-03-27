"""
NinjaOne async client wrapper.

Compatibility wrapper around the generated async SDK (sdk.py).

Usage:
    from workflows.ninjaone import client as ninja

    async def my_workflow():
        devices = await ninja.list_devices()
        org = await ninja.get_organization(org_id="123")

The generated SDK handles OAuth credential fetching from the NinjaOne
Bifrost integration automatically.
"""

from typing import Any

from . import sdk as _sdk


def __getattr__(name: str) -> Any:
    """
    Preserve the historical `workflows.ninjaone.client` import path.

    This means `from workflows.ninjaone import client as ninja` followed by
    `await ninja.list_devices()` works without manually listing every method.
    """
    if name.startswith("_"):
        raise AttributeError(name)
    if not hasattr(_sdk, name):
        raise AttributeError(f"NinjaOne SDK has no method '{name}'")
    return getattr(_sdk, name)
