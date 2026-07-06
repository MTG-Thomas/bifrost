from src.services.webhooks.protocol import Deliver, SubscribeResult, WebhookAdapter
from src.services.webhooks.registry import AdapterRegistry


class RecordingAdapter(WebhookAdapter):
    name = "recording"
    display_name = "Recording Adapter"
    description = "Records registry interactions"
    requires_integration = "Example"
    config_schema = {"type": "object", "properties": {"mode": {"type": "string"}}}

    async def subscribe(self, callback_url, config, integration):
        return SubscribeResult(external_id="sub-1", state={"callback_url": callback_url})

    async def unsubscribe(self, external_id, state, integration):
        return None

    async def handle_request(self, request, config, state):
        return Deliver(data={"ok": True})


class ReplacementAdapter(RecordingAdapter):
    display_name = "Replacement Adapter"


class BrokenAdapter(RecordingAdapter):
    def __init__(self):
        raise RuntimeError("constructor failed")


def test_registry_defaults_to_generic_and_caches_instances():
    registry = AdapterRegistry()

    first = registry.get(None)
    second = registry.get("")

    assert first is second
    assert first.name == "generic"


def test_registry_reregistering_adapter_clears_cached_instance():
    registry = AdapterRegistry()
    registry.register("recording", RecordingAdapter)
    first = registry.get("recording")

    registry.register("recording", ReplacementAdapter)
    second = registry.get("recording")

    assert first is not second
    assert second.display_name == "Replacement Adapter"


def test_registry_returns_none_when_adapter_constructor_fails():
    registry = AdapterRegistry()
    registry.register("broken", BrokenAdapter)

    assert registry.get("broken") is None


def test_registry_lists_adapter_metadata_after_loading_custom_once(monkeypatch):
    registry = AdapterRegistry()
    registry.register("recording", RecordingAdapter)
    load_calls = 0

    def mark_loaded():
        nonlocal load_calls
        load_calls += 1
        registry._loaded_custom = True

    monkeypatch.setattr(registry, "_load_custom_adapters", mark_loaded)

    adapters = registry.list_adapters()
    recording = next(adapter for adapter in adapters if adapter["name"] == "recording")

    assert load_calls == 1
    assert recording == {
        "name": "recording",
        "display_name": "Recording Adapter",
        "description": "Records registry interactions",
        "requires_integration": "Example",
        "config_schema": {"type": "object", "properties": {"mode": {"type": "string"}}},
        "supports_renewal": False,
    }


def test_registry_get_adapter_info_returns_none_for_unknown_after_custom_load():
    registry = AdapterRegistry()

    assert registry.get_adapter_info("missing") is None
    assert registry._loaded_custom is True
