from __future__ import annotations

import hmac
from datetime import datetime, timezone

import pytest

from bifrost.webhooks import (
    Deliver,
    Rejected,
    SubscribeResult,
    ValidationResponse,
    WebhookAdapter,
    WebhookRequest,
    adapter,
)


def test_webhook_request_json_and_text_body() -> None:
    request = WebhookRequest(
        method="POST",
        headers={"content-type": "application/json"},
        query_params={"validationToken": "abc"},
        body=b'{"event": "created"}',
        source_ip="127.0.0.1",
    )

    assert request.json_body == {"event": "created"}
    assert request.text_body == '{"event": "created"}'


def test_webhook_request_json_body_returns_none_for_invalid_json() -> None:
    request = WebhookRequest(method="POST", headers={}, query_params={}, body=b"{")

    assert request.json_body is None
    assert request.text_body == "{"


def test_webhook_response_dataclasses() -> None:
    expires_at = datetime.now(timezone.utc)

    assert SubscribeResult(external_id="sub-1", state={"secret": "s"}, expires_at=expires_at)
    assert ValidationResponse(status_code=200, body="challenge").content_type == "text/plain"
    assert Deliver(data={"id": "1"}, event_type="created").raw_headers is None
    assert Rejected("bad signature", status_code=401).message == "bad signature"


class ExampleAdapter(WebhookAdapter):
    async def subscribe(self, callback_url, config, integration):
        return SubscribeResult(external_id="sub-1", state={"callback": callback_url})

    async def handle_request(self, request, config, state):
        return Deliver(data={"ok": True})


@pytest.mark.asyncio
async def test_webhook_adapter_default_unsubscribe_and_renew() -> None:
    adapter_instance = ExampleAdapter()

    assert await adapter_instance.unsubscribe("sub-1", {}, None) is None
    assert await adapter_instance.renew("sub-1", {}, None) is None


def test_generate_secret_uses_requested_hex_length() -> None:
    secret = WebhookAdapter.generate_secret(16)

    assert len(secret) == 16
    assert int(secret, 16) >= 0


def test_verify_hmac_sha256_accepts_valid_signature() -> None:
    payload = b'{"id": "evt_1"}'
    secret = "top-secret"
    digest = hmac.new(secret.encode(), payload, "sha256").hexdigest()

    assert WebhookAdapter.verify_hmac_sha256(payload, secret, f"sha256={digest}")
    assert WebhookAdapter.verify_hmac_sha256(payload, secret, digest, prefix="")
    assert not WebhookAdapter.verify_hmac_sha256(payload, secret, "sha256=bad")


def test_expiration_datetime_and_parse_datetime() -> None:
    raw = WebhookAdapter.expiration_datetime(minutes=5)
    parsed = WebhookAdapter.parse_datetime(raw)

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert WebhookAdapter.parse_datetime("2026-01-01T00:00:00Z") == datetime(
        2026, 1, 1, tzinfo=timezone.utc
    )
    assert WebhookAdapter.parse_datetime("not-a-date") is None


def test_adapter_decorator_with_explicit_name_and_integration() -> None:
    @adapter(name="graph", integration="Microsoft")
    class GraphAdapter(ExampleAdapter):
        pass

    assert GraphAdapter.name == "graph"
    assert GraphAdapter._adapter_metadata.name == "graph"
    assert GraphAdapter._adapter_metadata.integration == "Microsoft"


def test_adapter_decorator_derives_snake_case_name() -> None:
    @adapter
    class HaloPsaAdapter(ExampleAdapter):
        pass

    assert HaloPsaAdapter.name == "halo_psa"
    assert HaloPsaAdapter._adapter_metadata.name == "halo_psa"
