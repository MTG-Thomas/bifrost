from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from src.models import AIModelPricingCreate, AIModelPricingUpdate, InstallPackageRequest
from src.routers import ai_pricing, packages


def _user() -> SimpleNamespace:
    return SimpleNamespace(email="admin@example.com", user_id="user-1")


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(org_id="org-1")


def _db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _scalar_result(value: object) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars_all_result(values: list[object]) -> MagicMock:
    scalars = MagicMock()
    scalars.all.return_value = values
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


def _rows_result(rows: list[object]) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def _pricing_entry(**overrides: object) -> SimpleNamespace:
    data = {
        "id": 7,
        "provider": "openai",
        "model": "GPT 4o",
        "input_price_per_million": 5.0,
        "output_price_per_million": 15.0,
        "effective_date": date(2026, 7, 5),
        "created_at": datetime(2026, 7, 5, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 7, 5, tzinfo=timezone.utc),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_list_pricing_marks_used_entries_and_reports_missing_pricing() -> None:
    db = _db()
    db.execute = AsyncMock(
        side_effect=[
            _scalars_all_result([_pricing_entry()]),
            _rows_result(
                [
                    SimpleNamespace(provider="openai", model="GPT 4o"),
                    SimpleNamespace(provider="anthropic", model="Claude"),
                ]
            ),
        ]
    )

    result = await ai_pricing.list_pricing(_user(), db)

    assert result.pricing[0].is_used is True
    assert result.models_without_pricing == ["anthropic/Claude"]


@pytest.mark.asyncio
async def test_create_pricing_rejects_duplicate_provider_model() -> None:
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_result(_pricing_entry()))

    with pytest.raises(HTTPException) as exc:
        await ai_pricing.create_pricing(
            _user(),
            db,
            AIModelPricingCreate(
                provider="openai",
                model="GPT 4o",
                input_price_per_million=5,
                output_price_per_million=15,
            ),
        )

    assert exc.value.status_code == status.HTTP_409_CONFLICT
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_create_pricing_keeps_entry_when_backfill_fails() -> None:
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_result(None))
    db.refresh = AsyncMock(
        side_effect=lambda pricing: (
            setattr(pricing, "id", 8),
            setattr(pricing, "created_at", datetime(2026, 7, 5, tzinfo=timezone.utc)),
            setattr(pricing, "updated_at", datetime(2026, 7, 5, tzinfo=timezone.utc)),
        )
    )

    with (
        patch.object(ai_pricing, "get_shared_redis", AsyncMock(side_effect=RuntimeError("redis down"))),
    ):
        result = await ai_pricing.create_pricing(
            _user(),
            db,
            AIModelPricingCreate(
                provider="openai",
                model="GPT 4o",
                input_price_per_million=5,
                output_price_per_million=15,
            ),
        )

    assert result.provider == "openai"
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_pricing_only_updates_provided_fields() -> None:
    pricing = _pricing_entry(input_price_per_million=1.0, output_price_per_million=2.0)
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_result(pricing))

    result = await ai_pricing.update_pricing(
        7,
        _user(),
        db,
        AIModelPricingUpdate(input_price_per_million=3.5),
    )

    assert result.input_price_per_million == 3.5
    assert result.output_price_per_million == 2.0
    db.refresh.assert_awaited_once_with(pricing)


@pytest.mark.asyncio
async def test_delete_pricing_raises_not_found() -> None:
    db = _db()
    db.execute = AsyncMock(return_value=_scalar_result(None))

    with pytest.raises(HTTPException) as exc:
        await ai_pricing.delete_pricing(99, _user(), db)

    assert exc.value.status_code == status.HTTP_404_NOT_FOUND
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_packages_from_workers_merges_versions_and_skips_bad_entries() -> None:
    raw_redis = AsyncMock()
    raw_redis.scan = AsyncMock(return_value=(0, ["bifrost:pool:a", "bifrost:pool:a:heartbeat", "bifrost:pool:b"]))
    raw_redis.hget = AsyncMock(
        side_effect=[
            '[{"name": "HTTPX", "version": "0.27.0"}, {"name": "pytest", "version": "8.0.0"}]',
            '[{"name": "httpx", "version": "0.28.0"}]',
        ]
    )
    redis_client = SimpleNamespace(_get_redis=AsyncMock(return_value=raw_redis))

    with patch.object(packages, "get_redis_client", return_value=redis_client):
        result = await packages.get_packages_from_workers()

    assert [(pkg.name, pkg.version) for pkg in result or []] == [
        ("httpx", "0.28.0"),
        ("pytest", "8.0.0"),
    ]
    assert raw_redis.hget.await_count == 2


@pytest.mark.asyncio
async def test_get_installed_packages_falls_back_to_local_when_no_workers() -> None:
    with (
        patch.object(packages, "get_packages_from_workers", AsyncMock(return_value=None)),
        patch.object(
            packages,
            "get_installed_packages_local",
            AsyncMock(return_value=[packages.InstalledPackage(name="fastapi", version="1.0")]),
        ) as local,
    ):
        result = await packages.get_installed_packages()

    assert result[0].name == "fastapi"
    local.assert_awaited_once()


@pytest.mark.asyncio
async def test_install_package_warms_cache_on_miss_then_broadcasts_recycle() -> None:
    with (
        patch.object(packages, "get_requirements", AsyncMock(side_effect=[None, {"content": "fastapi==1\n"}])),
        patch.object(packages, "warm_requirements_cache", AsyncMock()) as warm,
        patch.object(
            packages,
            "append_package_to_requirements",
            return_value=("fastapi==1\nhttpx==0.28.0\n", False),
        ) as append,
        patch.object(packages, "save_requirements", AsyncMock()) as save,
        patch.object(packages, "publish_broadcast", AsyncMock()) as publish,
    ):
        result = await packages.install_package(
            InstallPackageRequest(package_name="httpx", version="0.28.0"),
            _ctx(),
            _user(),
        )

    assert result.status == "success"
    warm.assert_awaited_once()
    append.assert_called_once_with("fastapi==1\n", "httpx", "0.28.0")
    save.assert_awaited_once_with("fastapi==1\nhttpx==0.28.0\n")
    assert publish.await_args.kwargs["message"]["package"] == "httpx"


@pytest.mark.asyncio
async def test_uninstall_package_reports_absent_package_but_recycles_workers() -> None:
    with (
        patch.object(packages, "get_requirements", AsyncMock(return_value={"content": "fastapi==1\n"})),
        patch.object(
            packages,
            "remove_package_from_requirements",
            return_value=("fastapi==1\n", False),
        ),
        patch.object(packages, "save_requirements", AsyncMock()) as save,
        patch.object(packages, "publish_broadcast", AsyncMock()) as publish,
    ):
        result = await packages.uninstall_package("httpx", _ctx(), _user())

    assert result["was_present"] is False
    save.assert_not_awaited()
    assert publish.await_args.kwargs["message"]["action"] == "uninstall"
