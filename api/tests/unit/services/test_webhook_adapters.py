"""
Unit tests for webhook adapter authentication.

Tests HMAC-SHA256 signature verification and the GenericWebhookAdapter
request handling logic.
"""

import hashlib
import hmac
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.services.webhooks.adapters.generic import GenericWebhookAdapter
from src.services.webhooks.adapters.local_fixture import LocalFixtureWebhookAdapter
from src.services.webhooks.adapters.microsoft_graph import (
    MicrosoftGraphAdapter,
    _get_access_token,
)
from src.services.webhooks.protocol import (
    Deliver,
    Rejected,
    RenewResult,
    ValidationResponse,
    WebhookAdapter,
    WebhookRequest,
)
from src.services.webhooks import registry as webhook_registry


def _sign(body: bytes, secret: str, prefix: str = "sha256=") -> str:
    """Helper to compute HMAC-SHA256 signature."""
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"{prefix}{sig}"


def _make_request(
    body: bytes = b'{"event": "test"}',
    headers: dict | None = None,
) -> WebhookRequest:
    """Helper to build a WebhookRequest."""
    return WebhookRequest(
        method="POST",
        path="/webhook/test",
        headers=headers or {},
        body=body,
        query_params={},
    )


@pytest.mark.asyncio
async def test_local_fixture_adapter_renews_deterministically():
    adapter = LocalFixtureWebhookAdapter()

    result = await adapter.renew(
        external_id="local-scheduler-fixture",
        state={"renewal_count": 2},
        integration=None,
    )

    assert result is not None
    assert result.state == {"renewal_count": 3}
    assert result.expires_at is not None
    assert result.expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_local_fixture_adapter_rejects_unknown_subscription():
    adapter = LocalFixtureWebhookAdapter()

    result = await adapter.renew(
        external_id="not-the-fixture",
        state={},
        integration=None,
    )

    assert result is None


def test_local_fixture_adapter_is_registered_only_outside_production(monkeypatch):
    monkeypatch.setattr(
        webhook_registry,
        "get_settings",
        lambda: SimpleNamespace(environment="development"),
    )
    assert webhook_registry.AdapterRegistry().get("local_fixture") is not None

    monkeypatch.setattr(
        webhook_registry,
        "get_settings",
        lambda: SimpleNamespace(environment="production"),
    )
    assert webhook_registry.AdapterRegistry().get("local_fixture") is None


# =============================================================================
# TestVerifyHmacSha256 - WebhookAdapter.verify_hmac_sha256()
# =============================================================================


class TestVerifyHmacSha256:
    """Tests for the static verify_hmac_sha256 helper."""

    def test_valid_signature(self):
        body = b"hello world"
        secret = "mysecret"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        assert WebhookAdapter.verify_hmac_sha256(body, secret, sig) is True

    def test_invalid_signature(self):
        body = b"hello world"
        secret = "mysecret"

        assert WebhookAdapter.verify_hmac_sha256(body, secret, "bad") is False

    def test_prefix_stripping(self):
        body = b"hello world"
        secret = "mysecret"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        assert (
            WebhookAdapter.verify_hmac_sha256(
                body, secret, f"sha256={sig}", prefix="sha256="
            )
            is True
        )

    def test_empty_prefix(self):
        body = b"hello world"
        secret = "mysecret"
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

        assert (
            WebhookAdapter.verify_hmac_sha256(body, secret, sig, prefix="")
            is True
        )

    def test_none_signature_returns_false(self):
        assert (
            WebhookAdapter.verify_hmac_sha256(b"body", "secret", None) is False
        )


# =============================================================================
# TestGenericWebhookAdapterHandleRequest
# =============================================================================


class TestGenericWebhookAdapterHandleRequest:
    """Tests for GenericWebhookAdapter.handle_request()."""

    @pytest.fixture
    def adapter(self):
        return GenericWebhookAdapter()

    @pytest.mark.asyncio
    async def test_no_secret_accepts_any_request(self, adapter):
        """No secret in state → delivers without checking signature."""
        request = _make_request()
        result = await adapter.handle_request(request, config={}, state={})

        assert isinstance(result, Deliver)

    @pytest.mark.asyncio
    async def test_valid_signature_accepted(self, adapter):
        """Valid HMAC signature → Deliver."""
        body = b'{"event": "push"}'
        secret = "test-secret"
        sig = _sign(body, secret)

        request = _make_request(
            body=body,
            headers={"x-signature-256": sig},
        )
        result = await adapter.handle_request(
            request, config={}, state={"secret": secret}
        )

        assert isinstance(result, Deliver)

    @pytest.mark.asyncio
    async def test_missing_signature_rejected(self, adapter):
        """Secret set but no signature header → Rejected(401)."""
        request = _make_request(headers={})
        result = await adapter.handle_request(
            request, config={}, state={"secret": "mysecret"}
        )

        assert isinstance(result, Rejected)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_signature_rejected(self, adapter):
        """Bad HMAC → Rejected(401)."""
        request = _make_request(
            headers={"x-signature-256": "sha256=badhash"},
        )
        result = await adapter.handle_request(
            request, config={}, state={"secret": "mysecret"}
        )

        assert isinstance(result, Rejected)
        assert result.status_code == 401

    @pytest.mark.asyncio
    async def test_custom_signature_header(self, adapter):
        """Reads signature from custom header name."""
        body = b'{"data": 1}'
        secret = "s3cret"
        sig = _sign(body, secret)

        request = _make_request(
            body=body,
            headers={"x-hub-signature-256": sig},
        )
        result = await adapter.handle_request(
            request,
            config={"signature_header": "X-Hub-Signature-256"},
            state={"secret": secret},
        )

        assert isinstance(result, Deliver)

    @pytest.mark.asyncio
    async def test_custom_signature_prefix(self, adapter):
        """Handles different prefix."""
        body = b'{"data": 1}'
        secret = "s3cret"
        sig = _sign(body, secret, prefix="hmac-sha256=")

        request = _make_request(
            body=body,
            headers={"x-signature-256": sig},
        )
        result = await adapter.handle_request(
            request,
            config={"signature_prefix": "hmac-sha256="},
            state={"secret": secret},
        )

        assert isinstance(result, Deliver)

    @pytest.mark.asyncio
    async def test_event_type_from_header(self, adapter):
        """Extracts event type from header."""
        request = _make_request(
            headers={"x-event-type": "push"},
        )
        result = await adapter.handle_request(
            request,
            config={"event_type_header": "X-Event-Type"},
            state={},
        )

        assert isinstance(result, Deliver)
        assert result.event_type == "push"

    @pytest.mark.asyncio
    async def test_event_type_from_payload_field(self, adapter):
        """Extracts event type from JSON field."""
        request = _make_request(
            body=b'{"type": "invoice.paid"}',
        )
        result = await adapter.handle_request(
            request,
            config={"event_type_field": "type"},
            state={},
        )

        assert isinstance(result, Deliver)
        assert result.event_type == "invoice.paid"

    @pytest.mark.asyncio
    async def test_event_type_field_overrides_header(self, adapter):
        """Payload field takes precedence over header."""
        request = _make_request(
            body=b'{"type": "from_field"}',
            headers={"x-event-type": "from_header"},
        )
        result = await adapter.handle_request(
            request,
            config={
                "event_type_header": "X-Event-Type",
                "event_type_field": "type",
            },
            state={},
        )

        assert isinstance(result, Deliver)
        assert result.event_type == "from_field"


# =============================================================================
# TestGenericWebhookAdapterSubscribe
# =============================================================================


class TestGenericWebhookAdapterSubscribe:
    """Tests for GenericWebhookAdapter.subscribe()."""

    @pytest.fixture
    def adapter(self):
        return GenericWebhookAdapter()

    @pytest.mark.asyncio
    async def test_subscribe_stores_secret_in_state(self, adapter):
        result = await adapter.subscribe(
            callback_url="https://example.com/webhook",
            config={"secret": "my-secret-key"},
            integration=None,
        )

        assert result.state["secret"] == "my-secret-key"

    @pytest.mark.asyncio
    async def test_subscribe_without_secret_empty_state(self, adapter):
        result = await adapter.subscribe(
            callback_url="https://example.com/webhook",
            config={},
            integration=None,
        )

        assert result.state == {}


# =============================================================================
# TestMicrosoftGraphAdapter
# =============================================================================


class TestMicrosoftGraphAdapter:
    """Tests for MicrosoftGraphAdapter Graph-specific behavior."""

    @pytest.fixture
    def adapter(self):
        return MicrosoftGraphAdapter()

    def test_get_access_token_reads_sdk_oauth(self):
        integration = SimpleNamespace(
            oauth=SimpleNamespace(access_token="sdk-token"),
        )

        assert _get_access_token(integration) == "sdk-token"

    def test_get_access_token_decrypts_orm_token(self, monkeypatch):
        monkeypatch.setattr(
            "src.services.webhooks.adapters.microsoft_graph.decrypt_secret",
            lambda value: f"decrypted:{value}",
        )
        integration = SimpleNamespace(
            oauth_provider=SimpleNamespace(
                tokens=[SimpleNamespace(encrypted_access_token=b"encrypted")]
            )
        )

        assert _get_access_token(integration) == "decrypted:encrypted"

    @pytest.mark.parametrize("integration", [None, SimpleNamespace()])
    def test_get_access_token_requires_integration_and_token(self, integration):
        with pytest.raises(ValueError):
            _get_access_token(integration)

    @pytest.mark.asyncio
    async def test_validation_token_returns_plain_text_response(self, adapter):
        request = WebhookRequest(
            method="GET",
            path="/api/hooks/source-id",
            headers={},
            query_params={"validationToken": "probe-token"},
            body=b"",
        )

        result = await adapter.handle_request(request, config={}, state={})

        assert isinstance(result, ValidationResponse)
        assert result.status_code == 200
        assert result.body == "probe-token"
        assert result.content_type == "text/plain"

    @pytest.mark.asyncio
    async def test_subscribe_reads_orm_oauth_token(self, adapter, monkeypatch):
        calls = []

        class FakeResponse:
            status_code = 201

            def json(self):
                return {
                    "id": "graph-subscription-id",
                    "expirationDateTime": "2026-05-16T12:00:00Z",
                }

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, headers, json, timeout):
                calls.append({
                    "url": url,
                    "headers": headers,
                    "json": json,
                    "timeout": timeout,
                })
                return FakeResponse()

        monkeypatch.setattr(
            "src.services.webhooks.adapters.microsoft_graph.decrypt_secret",
            lambda encrypted: "decrypted-access-token",
        )
        monkeypatch.setattr(
            "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
            FakeClient,
        )

        integration = SimpleNamespace(
            oauth_provider=SimpleNamespace(
                tokens=[
                    SimpleNamespace(encrypted_access_token=b"encrypted-token"),
                ]
            )
        )

        result = await adapter.subscribe(
            callback_url="https://bifrost.example.com/api/hooks/source-id",
            config={
                "resource": "/users/midbot@midtowntg.com/messages",
                "change_types": ["created"],
            },
            integration=integration,
        )

        assert result.external_id == "graph-subscription-id"
        assert result.state["client_state"]
        assert calls[0]["headers"]["Authorization"] == "Bearer decrypted-access-token"
        assert calls[0]["json"]["notificationUrl"] == "https://bifrost.example.com/api/hooks/source-id"

    @pytest.mark.asyncio
    async def test_dynamic_values_lists_users(self, adapter, monkeypatch):
        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "value": [
                        {
                            "id": "user-1",
                            "displayName": "Ada",
                            "mail": "ada@example.com",
                            "userPrincipalName": "ada@tenant.example",
                        },
                        {
                            "id": "user-2",
                            "displayName": None,
                            "mail": None,
                            "userPrincipalName": "grace@tenant.example",
                        },
                    ]
                }

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, url, headers, params, timeout):
                assert url == "https://graph.microsoft.com/v1.0/users"
                assert headers == {"Authorization": "Bearer sdk-token"}
                assert params["$top"] == "100"
                assert timeout == 30.0
                return FakeResponse()

        monkeypatch.setattr(
            "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
            FakeClient,
        )

        users = await adapter.get_dynamic_values(
            "list_users",
            SimpleNamespace(oauth=SimpleNamespace(access_token="sdk-token")),
            {},
        )

        assert users == [
            {"id": "user-1", "displayName": "Ada", "mail": "ada@example.com"},
            {"id": "user-2", "displayName": "grace@tenant.example", "mail": None},
        ]

    async def test_dynamic_values_list_users_surfaces_graph_error(
        self, adapter, monkeypatch
    ):
        class FakeResponse:
            status_code = 400
            text = "bad request"

            def json(self):
                return {"error": {"message": "permission denied"}}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, *args, **kwargs):
                return FakeResponse()

        monkeypatch.setattr(
            "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
            FakeClient,
        )

        with pytest.raises(ValueError, match="permission denied"):
            await adapter.get_dynamic_values(
                "list_users",
                SimpleNamespace(oauth=SimpleNamespace(access_token="sdk-token")),
                {},
            )

    async def test_dynamic_values_lists_resources_and_rejects_unknown_operation(
        self, adapter
    ):
        resources = await adapter.get_dynamic_values(
            "list_resources",
            integration=None,
            current_config={"user_id": "user-1"},
        )

        assert resources[0]["value"] == "/users/user-1/messages"
        assert any(r["value"] == "/communications/callRecords" for r in resources)

        with pytest.raises(NotImplementedError):
            await adapter.get_dynamic_values("missing", None, {})

    @pytest.mark.asyncio
    async def test_subscribe_includes_resource_data_and_surfaces_error(
        self, adapter, monkeypatch
    ):
        class FakeResponse:
            status_code = 400
            text = "raw graph error"

            def json(self):
                return {"error": {"message": "subscription rejected"}}

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def post(self, url, headers, json, timeout):
                assert json["includeResourceData"] is True
                return FakeResponse()

        monkeypatch.setattr(
            "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
            FakeClient,
        )

        with pytest.raises(ValueError, match="subscription rejected"):
            await adapter.subscribe(
                callback_url="https://example.com/hook",
                config={
                    "resource": "/users/user-1/messages",
                    "change_types": ["created", "updated"],
                    "include_resource_data": True,
                },
                integration=SimpleNamespace(oauth=SimpleNamespace(access_token="token")),
            )

    @pytest.mark.asyncio
    async def test_unsubscribe_is_best_effort(self, adapter, monkeypatch):
        calls = []

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def delete(self, url, headers, timeout):
                calls.append((url, headers, timeout))
                raise RuntimeError("already gone")

        monkeypatch.setattr(
            "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
            FakeClient,
        )

        await adapter.unsubscribe(
            "sub-1",
            {},
            SimpleNamespace(oauth=SimpleNamespace(access_token="token")),
        )
        await adapter.unsubscribe(None, {}, SimpleNamespace())
        await adapter.unsubscribe("sub-2", {}, None)

        assert calls == [
            (
                "https://graph.microsoft.com/v1.0/subscriptions/sub-1",
                {"Authorization": "Bearer token"},
                30.0,
            )
        ]

    @pytest.mark.asyncio
    async def test_renew_success_failure_and_missing_inputs(self, adapter, monkeypatch):
        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def patch(self, url, headers, json, timeout):
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"expirationDateTime": "2026-05-16T12:00:00Z"},
                )

        monkeypatch.setattr(
            "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
            FakeClient,
        )

        renewed = await adapter.renew(
            "sub-1",
            {},
            SimpleNamespace(oauth=SimpleNamespace(access_token="token")),
        )
        assert isinstance(renewed, RenewResult)
        assert renewed.expires_at is not None
        assert renewed.expires_at.isoformat().startswith("2026-05-16T12:00:00")

        assert await adapter.renew(None, {}, SimpleNamespace()) is None
        assert await adapter.renew("sub-2", {}, None) is None

    @pytest.mark.asyncio
    async def test_renew_returns_none_on_graph_failure(self, adapter, monkeypatch):
        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def patch(self, *args, **kwargs):
                return SimpleNamespace(status_code=404, json=lambda: {})

        monkeypatch.setattr(
            "src.services.webhooks.adapters.microsoft_graph.httpx.AsyncClient",
            FakeClient,
        )

        assert (
            await adapter.renew(
                "sub-1",
                {},
                SimpleNamespace(oauth=SimpleNamespace(access_token="token")),
            )
            is None
        )

    @pytest.mark.asyncio
    async def test_handle_request_rejects_invalid_notifications(self, adapter):
        invalid_payload = await adapter.handle_request(
            WebhookRequest(
                method="POST",
                path="/hook",
                headers={},
                query_params={},
                body=b"",
            ),
            config={},
            state={},
        )
        assert isinstance(invalid_payload, Rejected)
        assert invalid_payload.status_code == 400

        no_notifications = await adapter.handle_request(
            WebhookRequest(
                method="POST",
                path="/hook",
                headers={},
                query_params={},
                body=b'{"value": []}',
            ),
            config={},
            state={},
        )
        assert isinstance(no_notifications, Rejected)
        assert no_notifications.status_code == 400

        bad_state = await adapter.handle_request(
            WebhookRequest(
                method="POST",
                path="/hook",
                headers={},
                query_params={},
                body=b'{"value": [{"clientState": "bad"}]}',
            ),
            config={},
            state={"client_state": "expected"},
        )
        assert isinstance(bad_state, Rejected)
        assert bad_state.status_code == 401

    @pytest.mark.asyncio
    async def test_handle_request_delivers_first_notification(self, adapter):
        result = await adapter.handle_request(
            WebhookRequest(
                method="POST",
                path="/hook",
                headers={"x-ms-signature": "sig"},
                query_params={},
                body=(
                    b'{'
                    b'"value": [{'
                    b'"subscriptionId": "sub-1",'
                    b'"resource": "/users/user-1/messages",'
                    b'"changeType": "created",'
                    b'"clientState": "expected",'
                    b'"tenantId": "tenant-1",'
                    b'"resourceData": {"id": "message-1"}'
                    b"}]"
                    b"}"
                ),
                _json_cache={
                    "value": [
                        {
                            "subscriptionId": "sub-1",
                            "resource": "/users/user-1/messages",
                            "changeType": "created",
                            "clientState": "expected",
                            "tenantId": "tenant-1",
                            "resourceData": {"id": "message-1"},
                        }
                    ]
                },
            ),
            config={},
            state={"client_state": "expected"},
        )

        assert isinstance(result, Deliver)
        assert result.event_type == "messages.created"
        assert result.data["subscription_id"] == "sub-1"
        assert result.data["resource_data"] == {"id": "message-1"}
        assert result.raw_headers == {"x-ms-signature": "sig"}
