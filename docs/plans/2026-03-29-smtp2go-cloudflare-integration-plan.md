# SMTP2Go + Cloudflare Integration Plan

## Context

This plan follows Bifrost's established first-class vendor integration pattern:

- thin vendor client in `modules/{vendor}.py`
- mapping picker data provider in `features/{vendor}/workflows/data_providers.py`
- idempotent sync workflow in `features/{vendor}/workflows/sync_*.py`
- tactical metadata wiring in `.bifrost/integrations.yaml` and `.bifrost/workflows.yaml`
- unit tests in `api/tests/unit/`

The goal is to support organization mapping and ongoing sync for:

1. SMTP2Go accounts/customers (for tenant-level mail service ownership)
2. Cloudflare accounts/zones (for DNS + edge tenancy ownership)

## Integration 1: SMTP2Go

## 1) Proposed entity model

- **Primary mapped entity:** SMTP2Go customer/account (not individual senders or domains)
- `entity_id`: vendor customer/account ID
- `entity_name`: customer/account display name
- optional `config`: summary context (e.g., domain count, plan tier) if surfaced by API

Rationale: this aligns with Bifrost org mapping semantics and avoids unstable per-domain/per-mailbox mapping.

## 2) Files to add

- `modules/smtp2go.py`
  - async `httpx` client
  - auth with global API key secret
  - list customers/accounts helper
  - normalize helper returning stable `{id, name, ...}`
  - `get_client(scope: str | None = None)` using integration config/secrets

- `features/smtp2go/workflows/data_providers.py`
  - `SMTP2Go: List Accounts` provider
  - returns sorted `{value, label}` for mapping UI

- `features/smtp2go/workflows/sync_accounts.py`
  - `SMTP2Go: Sync Accounts` workflow
  - list vendor accounts
  - match/create Bifrost org by normalized name
  - `integrations.upsert_mapping("SMTP2Go", ...)`
  - idempotent behavior + error collection summary

- `features/smtp2go/__init__.py`

## 3) Metadata wiring

- `.bifrost/integrations.yaml`
  - add `SMTP2Go` integration entry
  - config schema:
    - `api_key` (secret, required)
    - optional `base_url` if SMTP2Go has region/alt endpoint requirements
  - set `list_entities_data_provider_id` to SMTP2Go data-provider UUID

- `.bifrost/workflows.yaml`
  - register SMTP2Go data provider and sync workflow entries

## 4) Unit tests (SMTP2Go)

Add `api/tests/unit/test_smtp2go_integration.py` covering:

- config contract validation (required keys / missing key handling)
- API payload normalization and deterministic sorting
- sync behavior:
  - creates new org if no name match
  - reuses existing org on name match
  - skips already-mapped `entity_id`
  - upsert payload correctness
  - partial-failure reporting without halting entire sync

---

## Integration 2: Cloudflare

## 1) Proposed entity model

- **Primary mapped entity:** Cloudflare account (preferred)
- **Secondary context:** zone IDs attached in mapping `config` when useful
- `entity_id`: Cloudflare account ID
- `entity_name`: account name
- optional `config`: `zone_ids`, `zone_count`, or API metadata needed by downstream workflows

Rationale: account-level mapping best represents customer tenancy. Zones can be numerous and better treated as subordinate resources.

## 2) Files to add

- `modules/cloudflare.py`
  - async `httpx` client
  - auth via API token (Bearer)
  - pagination helpers for accounts/zones
  - normalize account and zone helpers
  - optional retry/backoff for rate-limit responses
  - `get_client(scope: str | None = None)` via integration config

- `features/cloudflare/workflows/data_providers.py`
  - `Cloudflare: List Accounts`
  - sorted `{value, label}` options; labels may include zone counts

- `features/cloudflare/workflows/sync_accounts.py`
  - `Cloudflare: Sync Accounts`
  - list accounts
  - collect per-account zone context (optional)
  - match/create Bifrost organizations
  - upsert mappings idempotently

- `features/cloudflare/__init__.py`

## 3) Metadata wiring

- `.bifrost/integrations.yaml`
  - add `Cloudflare` integration entry
  - config schema:
    - `api_token` (secret, required)
    - optional `account_filter` (string/list) for scoped MSP rollouts

- `.bifrost/workflows.yaml`
  - register Cloudflare data provider + sync workflow

## 4) Unit tests (Cloudflare)

Add `api/tests/unit/test_cloudflare_integration.py` covering:

- pagination + normalization behavior
- sorting guarantees for data provider options
- sync idempotency and mapping upserts
- handling missing/empty account names (fallback label rules)
- rate-limit and transient error behavior (retry + bounded failure reporting)

---

## Shared implementation decisions

## Naming + category conventions

- Workflow categories: `SMTP2Go` and `Cloudflare`
- Tags include vendor + `sync` or `data-provider`
- Function names should reflect account-based mapping (`sync_smtp2go_accounts`, `list_cloudflare_accounts`)

## Secret/config handling

- Prefer integration config + `secrets.get` access in each vendor module
- Keep auth construction in module layer; avoid auth logic in workflows

## Mapping behavior contract

- Name-based org match should be case-insensitive and trimmed
- mapping upserts should not overwrite unrelated `config` fields destructively
- repeat sync runs must produce stable counts and no duplicate mappings

## Rollout strategy

1. Implement SMTP2Go first (smaller surface area, validates pattern quickly)
2. Implement Cloudflare second (pagination + zone context)
3. Enable mapping in lower environment with a narrow account subset
4. Observe sync output + mapping quality
5. Expand scope after validation

## Validation commands (when implementing)

Preferred:

- `./test.sh tests/unit/test_smtp2go_integration.py`
- `./test.sh tests/unit/test_cloudflare_integration.py`
- `./test.sh tests/unit -k "smtp2go or cloudflare"`

If Docker-backed tests are unavailable, run partial local checks and clearly mark validation as partial.

## Deliverables checklist

- [ ] `modules/smtp2go.py`
- [ ] `features/smtp2go/workflows/data_providers.py`
- [ ] `features/smtp2go/workflows/sync_accounts.py`
- [ ] `modules/cloudflare.py`
- [ ] `features/cloudflare/workflows/data_providers.py`
- [ ] `features/cloudflare/workflows/sync_accounts.py`
- [ ] `.bifrost/integrations.yaml` entries for SMTP2Go + Cloudflare
- [ ] `.bifrost/workflows.yaml` entries for data providers + sync workflows
- [ ] `api/tests/unit/test_smtp2go_integration.py`
- [ ] `api/tests/unit/test_cloudflare_integration.py`
