# Integration Config Secrets Fix

## Date: 2026-02-19

## Problem

Two issues with integration configuration management:

1. **`_save_config()` in `integrations.py` doesn't set `config_type` or encrypt secrets.** When integration defaults or per-org overrides are saved through the integrations router, the `ConfigModel` entries get `config_type=STRING` regardless of the schema type. The Config router (`config.py`) handles this correctly—it checks the type, sets `config_type`, and calls `encrypt_secret()` for secrets. But the integrations router bypasses all of that.

2. **The Configuration list page doesn't show which integration a config belongs to.** Configs created through integrations have `integration_id` set on the ORM model, but this isn't exposed in `ConfigResponse` or shown in the UI.

## Design

### 1. Fix `_save_config()` to set `config_type` and encrypt secrets

**File:** `api/src/routers/integrations.py` — `IntegrationsRepository._save_config()`

The method already fetches `schema_items` and has `schema_item.type` available. Changes:

- Map `schema_item.type` string to `ConfigTypeEnum` (e.g. `"secret"` → `ConfigTypeEnum.SECRET`)
- If type is `SECRET`, encrypt value using `encrypt_secret()` before storing
- Set `config_type` on both INSERT and UPDATE paths
- If no schema item exists for a key, default to `ConfigTypeEnum.STRING`

### 2. Fix read paths to decrypt secrets

**File:** `api/src/routers/integrations.py` — `get_integration_defaults()`, `get_org_config_overrides()`, `get_all_org_config_overrides()`

When reading config values for SDK consumption:
- Check if `entry.config_type == ConfigTypeEnum.SECRET`
- If so, call `decrypt_secret()` on the stored value before returning
- This ensures workflows get usable secret values

### 3. Add integration info to `ConfigResponse`

**File:** `api/src/models/contracts/config.py` — `ConfigResponse`
- Add `integration_id: str | None = None`
- Add `integration_name: str | None = None`

**File:** `api/src/routers/config.py` — `ConfigRepository.list_configs()`
- Join on `Config.integration_id` → `Integration` to get the integration name
- Populate `integration_id` and `integration_name` in the response

### 4. Show integration column in Config page UI

**File:** `client/src/pages/Config.tsx`
- Add "Integration" column showing integration name as a badge
- Regenerate types after backend changes

### 5. Data migration to backfill existing configs

**New Alembic migration:**
- Find all `configs` rows with `integration_id` set
- Join `integration_config_schema` on `(integration_id, key)` to find schema type
- For rows where schema type is `"secret"`: set `config_type='secret'` and encrypt the plaintext value
- For other rows: set `config_type` to match schema type

## Non-goals

- Changing how the Config router works (it's already correct)
- Adding encryption at rest beyond what `encrypt_secret()` provides
- Restructuring the two-tier config architecture
