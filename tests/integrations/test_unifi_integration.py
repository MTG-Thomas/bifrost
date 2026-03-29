from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bifrost import integrations, organizations
from features.unifi.workflows.data_providers import list_unifi_fabrics, list_unifi_sites
from features.unifi.workflows.sync_fabrics import sync_unifi_fabrics
from features.unifi.workflows.sync_sites import sync_unifi_sites
from modules import unifi


def _site(site_id: str | None, name: str | None, fabric_id: str | None = None) -> dict:
    payload: dict[str, str] = {
        "id": site_id or "",
        "name": name or "",
    }
    if fabric_id is not None:
        payload["fabricId"] = fabric_id
    return payload


def _fabric(fabric_id: str | None, name: str | None) -> dict:
    return {
        "id": fabric_id or "",
        "name": name or "",
    }


@pytest.mark.asyncio
async def test_get_client_uses_scoped_mapping(monkeypatch):
    async def fake_get(name: str, scope: str | None = None):
        assert name == "UniFi"
        assert scope == "org-123"
        return SimpleNamespace(config={"api_key": "key-123"}, entity_id="site-1")

    monkeypatch.setattr(integrations, "get", fake_get)

    client = await unifi.get_client(scope="org-123")
    try:
        assert client.site_id == "site-1"
        assert client._site_manager_base_url == unifi.UniFiClient.SITE_MANAGER_BASE_URL
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_get_client_requires_api_key(monkeypatch):
    async def fake_get(name: str, scope: str | None = None):
        return SimpleNamespace(config={}, entity_id=None)

    monkeypatch.setattr(integrations, "get", fake_get)

    with pytest.raises(RuntimeError, match="api_key"):
        await unifi.get_client(scope="global")


@pytest.mark.asyncio
async def test_list_unifi_sites_returns_sorted_options(monkeypatch):
    class FakeClient:
        def __init__(self) -> None:
            self.closed = False

        async def list_site_manager_sites(self):
            return [
                _site("2", "Zulu"),
                _site("1", "Alpha"),
                _site("", "Missing ID"),
                _site("3", ""),
            ]

        async def close(self) -> None:
            self.closed = True

    fake_client = FakeClient()

    async def fake_get_client(scope: str | None = None):
        assert scope == "global"
        return fake_client

    monkeypatch.setattr(unifi, "get_client", fake_get_client)

    result = await list_unifi_sites()

    assert result == [
        {"value": "1", "label": "Alpha"},
        {"value": "2", "label": "Zulu"},
    ]
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_list_unifi_fabrics_returns_sorted_options(monkeypatch):
    class FakeClient:
        def __init__(self) -> None:
            self.closed = False

        async def list_site_manager_fabrics(self):
            return [
                _fabric("fab-2", "Zulu Fabric"),
                _fabric("fab-1", "Alpha Fabric"),
                _fabric("", "Missing ID"),
                _fabric("fab-3", ""),
            ]

        async def close(self) -> None:
            self.closed = True

    fake_client = FakeClient()

    async def fake_get_client(scope: str | None = None):
        assert scope == "global"
        return fake_client

    monkeypatch.setattr(unifi, "get_client", fake_get_client)

    result = await list_unifi_fabrics()

    assert result == [
        {"value": "fab-1", "label": "Alpha Fabric"},
        {"value": "fab-2", "label": "Zulu Fabric"},
    ]
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_sync_unifi_sites_maps_unmapped_sites(monkeypatch):
    class FakeClient:
        def __init__(self) -> None:
            self.closed = False

        async def list_site_manager_sites(self):
            return [
                _site("site-100", "Already Mapped", "fab-100"),
                _site("site-200", "Existing Org", "fab-200"),
                _site("site-300", "New Org", None),
                _site(None, "Broken Site", None),
            ]

        async def close(self) -> None:
            self.closed = True

    fake_client = FakeClient()
    created_names: list[str] = []
    mapping_calls: list[tuple[str, str, str, str, dict | None]] = []

    async def fake_get_client(scope: str | None = None):
        assert scope == "global"
        return fake_client

    async def fake_list_mappings(name: str):
        assert name == "UniFi"
        return [SimpleNamespace(entity_id="site-100")]

    existing_org = SimpleNamespace(id="org-existing", name="Existing Org")

    async def fake_list_orgs():
        return [existing_org]

    async def fake_create_org(name: str):
        created_names.append(name)
        return SimpleNamespace(id="org-new", name=name)

    async def fake_upsert_mapping(
        name: str,
        *,
        scope: str,
        entity_id: str,
        entity_name: str,
        config: dict | None = None,
    ):
        mapping_calls.append((name, scope, entity_id, entity_name, config))

    monkeypatch.setattr(unifi, "get_client", fake_get_client)
    monkeypatch.setattr(integrations, "list_mappings", fake_list_mappings)
    monkeypatch.setattr(integrations, "upsert_mapping", fake_upsert_mapping)
    monkeypatch.setattr(organizations, "list", fake_list_orgs)
    monkeypatch.setattr(organizations, "create", fake_create_org)

    result = await sync_unifi_sites()

    assert result == {
        "total": 4,
        "mapped": 2,
        "already_mapped": 1,
        "created_orgs": 1,
        "errors": ["Skipped site with no ID: {'id': '', 'name': 'Broken Site'}"],
    }
    assert created_names == ["New Org"]
    assert mapping_calls == [
        ("UniFi", "org-existing", "site-200", "Existing Org", {"fabric_id": "fab-200"}),
        ("UniFi", "org-new", "site-300", "New Org", None),
    ]
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_sync_unifi_fabrics_maps_unmapped_fabrics(monkeypatch):
    class FakeClient:
        def __init__(self) -> None:
            self.closed = False

        async def list_site_manager_fabrics(self):
            return [
                _fabric("fab-100", "Already Mapped"),
                _fabric("fab-200", "Existing Org Fabric"),
                _fabric("fab-300", "New Org Fabric"),
                _fabric(None, "Broken Fabric"),
            ]

        async def close(self) -> None:
            self.closed = True

    fake_client = FakeClient()
    created_names: list[str] = []
    mapping_calls: list[tuple[str, str, str, str, dict | None]] = []

    async def fake_get_client(scope: str | None = None):
        assert scope == "global"
        return fake_client

    async def fake_list_mappings(name: str):
        assert name == "UniFi"
        return [SimpleNamespace(entity_id="fabric:fab-100")]

    existing_org = SimpleNamespace(id="org-existing", name="Existing Org Fabric")

    async def fake_list_orgs():
        return [existing_org]

    async def fake_create_org(name: str):
        created_names.append(name)
        return SimpleNamespace(id="org-new", name=name)

    async def fake_upsert_mapping(
        name: str,
        *,
        scope: str,
        entity_id: str,
        entity_name: str,
        config: dict | None = None,
    ):
        mapping_calls.append((name, scope, entity_id, entity_name, config))

    monkeypatch.setattr(unifi, "get_client", fake_get_client)
    monkeypatch.setattr(integrations, "list_mappings", fake_list_mappings)
    monkeypatch.setattr(integrations, "upsert_mapping", fake_upsert_mapping)
    monkeypatch.setattr(organizations, "list", fake_list_orgs)
    monkeypatch.setattr(organizations, "create", fake_create_org)

    result = await sync_unifi_fabrics()

    assert result == {
        "total": 4,
        "mapped": 2,
        "already_mapped": 1,
        "created_orgs": 1,
        "errors": ["Skipped fabric with no ID: {'id': '', 'name': 'Broken Fabric'}"],
    }
    assert created_names == ["New Org Fabric"]
    assert mapping_calls == [
        (
            "UniFi",
            "org-existing",
            "fabric:fab-200",
            "Existing Org Fabric",
            {"fabric_id": "fab-200", "mapping_kind": "fabric"},
        ),
        (
            "UniFi",
            "org-new",
            "fabric:fab-300",
            "New Org Fabric",
            {"fabric_id": "fab-300", "mapping_kind": "fabric"},
        ),
    ]
    assert fake_client.closed is True
