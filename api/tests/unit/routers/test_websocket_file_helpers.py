from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from src.core.principal import UserPrincipal
from src.routers import websocket as ws_mod


class FakeWebSocket:
    def __init__(self) -> None:
        self.state = SimpleNamespace()
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


def _user(*, org_id: uuid.UUID | None = None, is_superuser: bool = False) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid.uuid4(),
        email="user@example.com",
        organization_id=org_id,
        is_superuser=is_superuser,
    )


class TestParseChannels:
    def test_accepts_string_channels(self) -> None:
        parsed = ws_mod._parse_channels(["executions:abc"])

        assert parsed == [ws_mod.ChannelSpec(name="executions:abc", filter=None)]

    def test_accepts_object_channels_with_scope(self) -> None:
        parsed = ws_mod._parse_channels([
            {
                "name": "files:organization:docs",
                "scope": "global",
                "filter": {"eq": [{"row": "status"}, "open"]},
            }
        ])

        assert parsed[0].name == "files:organization:docs"
        assert parsed[0].scope == "global"
        assert parsed[0].filter is not None

    def test_rejects_invalid_channel_specs(self) -> None:
        with pytest.raises(ws_mod.WSError, match="channel must be"):
            ws_mod._parse_channels([{"filter": {"eq": ["a", "b"]}}])

    def test_rejects_invalid_filter_expression(self) -> None:
        with pytest.raises(ws_mod.WSError, match="invalid filter"):
            ws_mod._parse_channels([{"name": "table:abc", "filter": {"eq": ["only-one"]}}])


class TestFileChannelHelpers:
    @pytest.mark.parametrize(
        ("channel", "expected"),
        [
            ("files:workspace:docs/readme.md", ("workspace", "docs/readme.md")),
            ("files:organization:/docs/", ("organization", "docs")),
            ("file:organization:docs", None),
            ("files:organization", None),
        ],
    )
    def test_parse_file_channel(self, channel: str, expected: tuple[str, str] | None) -> None:
        assert ws_mod._parse_file_channel(channel) == expected

    @pytest.mark.parametrize(
        ("prefix", "path", "matches"),
        [
            ("", "anything/here.txt", True),
            ("docs", "docs", True),
            ("docs", "docs/readme.md", True),
            ("docs", "docs-old/readme.md", False),
            ("/docs/", "/docs/readme.md", True),
        ],
    )
    def test_path_matches_boundary(self, prefix: str, path: str, matches: bool) -> None:
        assert ws_mod._path_matches(prefix, path) is matches

    def test_file_org_and_scope_workspace_is_global(self) -> None:
        assert ws_mod._file_org_and_scope(
            user=_user(org_id=uuid.uuid4()),
            location="workspace",
            requested_scope="ignored",
        ) == (None, None)

    def test_file_org_and_scope_regular_user_defaults_to_own_org(self) -> None:
        org_id = uuid.uuid4()

        assert ws_mod._file_org_and_scope(
            user=_user(org_id=org_id),
            location="organization",
            requested_scope=None,
        ) == (org_id, str(org_id))

    def test_file_org_and_scope_regular_user_cannot_cross_org(self) -> None:
        assert ws_mod._file_org_and_scope(
            user=_user(org_id=uuid.uuid4()),
            location="organization",
            requested_scope=str(uuid.uuid4()),
        ) is None

    def test_file_org_and_scope_global_requires_platform_admin(self) -> None:
        assert ws_mod._file_org_and_scope(
            user=_user(org_id=uuid.uuid4()),
            location="organization",
            requested_scope="global",
        ) is None

        assert ws_mod._file_org_and_scope(
            user=_user(org_id=uuid.uuid4(), is_superuser=True),
            location="organization",
            requested_scope="global",
        ) == (None, "global")

    def test_file_org_and_scope_rejects_invalid_scope(self) -> None:
        assert ws_mod._file_org_and_scope(
            user=_user(org_id=uuid.uuid4(), is_superuser=True),
            location="organization",
            requested_scope="not-a-uuid",
        ) is None

    def test_file_channel_uses_global_sentinel(self) -> None:
        assert ws_mod._file_channel("workspace", None) == "files:workspace:GLOBAL"
        assert ws_mod._file_channel("organization", "global") == "files:organization:global"


@pytest.mark.asyncio
class TestAuthorizeFileSubscribe:
    async def test_rejects_invalid_file_channel(self) -> None:
        websocket = FakeWebSocket()

        result = await ws_mod._authorize_file_subscribe(
            websocket, _user(org_id=uuid.uuid4()), ws_mod.ChannelSpec("bad", None)
        )

        assert result is None
        assert websocket.sent == [
            {"type": "error", "channel": "bad", "message": "Invalid file channel"}
        ]

    async def test_rejects_scope_failures(self) -> None:
        websocket = FakeWebSocket()

        result = await ws_mod._authorize_file_subscribe(
            websocket,
            _user(org_id=uuid.uuid4()),
            ws_mod.ChannelSpec("files:organization:docs", None, scope="global"),
        )

        assert result is None
        assert websocket.sent == [
            {
                "type": "error",
                "channel": "files:organization:docs",
                "message": "Access denied",
            }
        ]

    async def test_rejects_when_no_policy_applies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def populate_roles(_: UserPrincipal) -> None:
            return None

        async def no_policy(**_: object) -> bool:
            return False

        monkeypatch.setattr(ws_mod, "_populate_user_roles", populate_roles)
        monkeypatch.setattr(ws_mod, "_file_has_applicable_policy", no_policy)
        websocket = FakeWebSocket()

        result = await ws_mod._authorize_file_subscribe(
            websocket,
            _user(org_id=uuid.uuid4()),
            ws_mod.ChannelSpec("files:organization:docs", None),
        )

        assert result is None
        assert websocket.sent[-1]["message"] == "Access denied"

    async def test_registers_subscription_and_dispatcher(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def populate_roles(_: UserPrincipal) -> None:
            return None

        async def has_policy(**_: object) -> bool:
            return True

        monkeypatch.setattr(ws_mod, "_populate_user_roles", populate_roles)
        monkeypatch.setattr(ws_mod, "_file_has_applicable_policy", has_policy)
        websocket = FakeWebSocket()
        org_id = uuid.uuid4()

        result = await ws_mod._authorize_file_subscribe(
            websocket,
            _user(org_id=org_id),
            ws_mod.ChannelSpec("files:organization:docs", None),
        )

        assert result == f"files:organization:{org_id}"
        assert getattr(websocket, "_file_dispatcher")
        assert websocket.state.file_subscriptions[
            f"files:organization:{org_id}:docs"
        ]["requested_channel"] == "files:organization:docs"


@pytest.mark.asyncio
class TestHandleFileMessage:
    async def test_ignores_unmatched_channels(self) -> None:
        websocket = FakeWebSocket()

        await ws_mod._handle_file_message(
            websocket, _user(org_id=uuid.uuid4()), "files:workspace:GLOBAL", {"type": "file_change"}
        )

        assert websocket.sent == []


@pytest.mark.asyncio
class TestHandleTableMessage:
    async def test_ignores_when_not_subscribed(self) -> None:
        websocket = FakeWebSocket()

        await ws_mod._handle_table_message(
            websocket,
            _user(org_id=uuid.uuid4()),
            "table:table-1",
            {"type": "document_change"},
        )

        assert websocket.sent == []

    async def test_policy_changed_invalidates_and_re_evaluates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        async def re_evaluate(websocket: FakeWebSocket, user: UserPrincipal, table_id: str) -> None:
            calls.append(table_id)

        monkeypatch.setattr(ws_mod, "_re_evaluate_subscription", re_evaluate)
        ws_mod._table_policy_cache["table-1"] = None
        websocket = FakeWebSocket()
        websocket.state.table_subscriptions = {
            "table-1": {"filter": None, "channel_name": "table:table-1"}
        }

        await ws_mod._handle_table_message(
            websocket,
            _user(org_id=uuid.uuid4()),
            "table:table-1",
            {"type": "policy_changed"},
        )

        assert calls == ["table-1"]
        assert "table-1" not in ws_mod._table_policy_cache

    async def test_re_evaluate_revokes_when_policies_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def no_policies(table_id: str):
            assert table_id == "table-1"
            return None

        monkeypatch.setattr(ws_mod, "_load_policies_for_table", no_policies)
        websocket = FakeWebSocket()
        websocket.state.table_subscriptions = {
            "table-1": {"filter": None, "channel_name": "table:table-1"}
        }

        await ws_mod._re_evaluate_subscription(
            websocket,
            _user(org_id=uuid.uuid4()),
            "table-1",
        )

        assert websocket.sent == [
            {"type": "subscription_revoked", "channel": "table:table-1"}
        ]
        assert websocket.state.table_subscriptions == {}

    async def test_document_change_emits_delete_decision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def policies(table_id: str):
            assert table_id == "table-1"
            return object()

        def decide(**_: object):
            return "delete", "row-1"

        monkeypatch.setattr(ws_mod, "_load_policies_for_table", policies)
        monkeypatch.setattr(ws_mod, "decide_visibility_change", decide)
        websocket = FakeWebSocket()
        websocket.state.table_subscriptions = {
            "table-1": {"filter": None, "channel_name": "table:table-1"}
        }

        await ws_mod._handle_table_message(
            websocket,
            _user(org_id=uuid.uuid4()),
            "table:table-1",
            {"type": "document_change", "old_row": {"id": "row-1"}, "new_row": None},
        )

        assert websocket.sent == [
            {
                "type": "document_change",
                "action": "delete",
                "table_id": "table-1",
                "row_id": "row-1",
            }
        ]

    async def test_document_change_emits_row_decision(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        row = {"id": "row-1", "status": "open"}

        async def policies(table_id: str):
            assert table_id == "table-1"
            return object()

        def decide(**_: object):
            return "update", row

        monkeypatch.setattr(ws_mod, "_load_policies_for_table", policies)
        monkeypatch.setattr(ws_mod, "decide_visibility_change", decide)
        websocket = FakeWebSocket()
        websocket.state.table_subscriptions = {
            "table-1": {"filter": None, "channel_name": "table:table-1"}
        }

        await ws_mod._handle_table_message(
            websocket,
            _user(org_id=uuid.uuid4()),
            "table:table-1",
            {"type": "document_change", "old_row": None, "new_row": row},
        )

        assert websocket.sent == [
            {
                "type": "document_change",
                "action": "update",
                "table_id": "table-1",
                "row": row,
            }
        ]

    async def test_revokes_subscription_when_policy_removed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def no_policy(**_: object) -> bool:
            return False

        monkeypatch.setattr(ws_mod, "_file_has_applicable_policy", no_policy)
        websocket = FakeWebSocket()
        websocket.state.file_subscriptions = {
            "files:organization:scope:docs": {
                "channel_name": "files:organization:scope",
                "requested_channel": "files:organization:docs",
                "organization_id": uuid.uuid4(),
                "location": "organization",
                "prefix": "docs",
            }
        }

        await ws_mod._handle_file_message(
            websocket,
            _user(org_id=uuid.uuid4()),
            "files:organization:scope",
            {"type": "file_policy_changed"},
        )

        assert websocket.sent == [
            {
                "type": "subscription_revoked",
                "channel": "files:organization:docs",
            }
        ]
        assert websocket.state.file_subscriptions == {}

    async def test_delivers_matching_allowed_file_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def allowed(**_: object) -> bool:
            return True

        monkeypatch.setattr(ws_mod, "_file_allowed", allowed)
        websocket = FakeWebSocket()
        org_id = uuid.uuid4()
        websocket.state.file_subscriptions = {
            f"files:organization:{org_id}:docs": {
                "channel_name": f"files:organization:{org_id}",
                "requested_channel": "files:organization:docs",
                "organization_id": org_id,
                "location": "organization",
                "prefix": "docs",
            }
        }

        await ws_mod._handle_file_message(
            websocket,
            _user(org_id=org_id),
            f"files:organization:{org_id}",
            {
                "type": "file_change",
                "location": "organization",
                "scope": str(org_id),
                "path": "/docs/readme.md",
                "action": "updated",
            },
        )

        assert websocket.sent == [
            {
                "type": "file_change",
                "channel": "files:organization:docs",
                "location": "organization",
                "scope": str(org_id),
                "path": "docs/readme.md",
                "action": "updated",
            }
        ]

    async def test_skips_non_matching_or_denied_file_change(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def denied(**_: object) -> bool:
            return False

        monkeypatch.setattr(ws_mod, "_file_allowed", denied)
        websocket = FakeWebSocket()
        websocket.state.file_subscriptions = {
            "files:organization:scope:docs": {
                "channel_name": "files:organization:scope",
                "requested_channel": "files:organization:docs",
                "organization_id": uuid.uuid4(),
                "location": "organization",
                "prefix": "docs",
            }
        }

        await ws_mod._handle_file_message(
            websocket,
            _user(org_id=uuid.uuid4()),
            "files:organization:scope",
            {"type": "file_change", "path": "other/readme.md"},
        )
        await ws_mod._handle_file_message(
            websocket,
            _user(org_id=uuid.uuid4()),
            "files:organization:scope",
            {"type": "file_change", "path": "docs/readme.md"},
        )

        assert websocket.sent == []
