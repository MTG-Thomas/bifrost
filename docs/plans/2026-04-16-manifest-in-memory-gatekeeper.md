# In-Memory Gatekeeper for `bifrost watch`

**Date:** 2026-04-16
**Status:** Draft — awaiting review
**Supersedes (partially):** `2026-02-17-manifest-source-of-truth.md`, `2026-03-11-incremental-manifest-import.md` (the incremental bulk-import path; watch no longer uses bulk import)
**Related:** `2026-02-19-cli-push-watch-broadcast.md`, `2026-02-05-bifrost-sdk-sync-and-skills.md`

---

## Context / Motivation

Entities created in the UI are sometimes silently deleted moments later. Reproducing the bug:

1. Jack creates an event source in the UI. Server commits the row and broadcasts `entity_change` (action=`add`) over the `file-activity` WebSocket channel.
2. Before the watcher's `_process_incoming` has drained that event and written the new UUID into `.bifrost/events.yaml`, Jack saves an unrelated workflow file in his editor.
3. The file save fires `_WatchChangeHandler.dispatch`, which enqueues changes. `_process_watch_batch` sees `.bifrost/` files among the dirty set (it always does — they're in the same tree) and posts the *current on-disk* manifest to `POST /api/files/manifest/import` with `delete_removed_entities=True` (cli.py:1896).
4. The server's `ManifestResolver._resolve_deletions` compares the incoming manifest (which lacks the just-created UUID) to the DB, concludes the UUID was "removed from manifest," and hard-deletes the row.

The core defect is that **`bifrost watch` treats `.bifrost/*.yaml` as an authoritative bulk manifest and pushes it through the same code path as `bifrost sync`'s intentional reconcile.** Any race between UI writes and watcher pushes produces silent deletions.

This plan redesigns watch mode so:

- `.bifrost/*.yaml` edits become **per-entity REST calls** against existing entity endpoints (`POST /api/events`, `PUT /api/events/{id}`, etc.).
- An **in-memory store** holds canonical parsed+normalized entity state and is the gatekeeper for every transition: disk reads, WebSocket events, outbound pushes.
- Text-level diffing is replaced by **structural diffing** on normalized Pydantic dumps keyed by UUID.
- The `_process_watch_batch` path **no longer calls `/api/files/manifest/import`**. Bulk manifest import remains, but is only invoked by the explicit `bifrost sync` TUI (interactive) and eventually by a future `bifrost import` command.

---

## What's NOT in scope

- **Full export/import split.** The longer-term direction — `bifrost export` / `bifrost import` as discrete commands that move artifacts between instances, and watch stopping touching `.bifrost/` entirely — is a separate follow-up plan.
- **Workspace reorganization.** `.bifrost/` layout, file naming, and directory structure are unchanged.
- **Tactical fix.** A separate same-day PR flips `delete_removed_entities=True` → `False` in `_process_watch_batch` as an urgent stopgap while this larger redesign is built.
- **Adding entity update endpoints.** A verification pass (see "Task 0: Compatibility Audit") confirmed every manifest entity type already has `POST`/`PATCH` (or `PUT`)/`DELETE` endpoints on its own router. No new server endpoints are required by this plan. The earlier draft's "Task 2: Add missing PUT endpoints" has been removed.
- **Bidirectional file sync** for non-manifest files (`apps/`, `workflows/`, `forms/`). Those remain on the existing per-file write/delete path.
- **`bifrost sync` TUI behavior.** The interactive one-shot sync keeps using `/api/files/manifest/import` because the user explicitly consents to deletions there.

---

## Architecture Overview

### Data flow

```
                  ┌──────────────────────────────────────────────┐
                  │           In-memory Entity Store             │
                  │                                              │
                  │   per entity_type → { uuid: CanonicalEntry } │
                  │                                              │
                  │   CanonicalEntry = {                         │
                  │     model: Pydantic instance                 │
                  │     dump:  normalized JSON (dict)            │
                  │     source: "server" | "disk" | "in-flight" │
                  │     last_pushed_hash: str | None             │
                  │   }                                          │
                  └──────┬───────────────┬───────────────┬───────┘
                         │               │               │
        ┌────────────────┘               │               └────────────────┐
        │                                │                                │
  ┌─────▼──────┐               ┌─────────▼────────┐             ┌─────────▼────────┐
  │ WebSocket  │               │  Filesystem      │             │   Outbound       │
  │ entity_    │   INCOMING    │  watchdog        │  OUTBOUND   │   REST calls     │
  │ change     │  (normalize   │  on_modified     │ (coalesced, │   POST / PUT /   │
  │ events     │  → diff →     │  on_created      │  serialized │   DELETE per-    │
  │            │   store →     │  on_deleted      │  per UUID)  │   entity + own   │
  │            │   write YAML) │  (parse YAML →   │             │   session header │
  │            │               │   normalize →    │             │                  │
  │            │               │   diff vs store) │             │                  │
  └────────────┘               └──────────────────┘             └──────────────────┘
        ▲                               │                                │
        │                               │                                │
        │                               ▼                                │
        │                     ┌─────────────────┐                        │
        │                     │  Startup fetch  │                        │
        └─────────────────────┤  GET /api/files │◄───────────────────────┘
                              │  /manifest      │  (authoritative
                              │                 │   initial state)
                              └─────────────────┘
```

### Inputs to the store
- **Startup:** `GET /api/files/manifest` → parse → normalize → seed store.
- **WebSocket entity_change:** from another session → normalize → diff vs store → update.
- **Filesystem:** user edits a `.bifrost/*.yaml` → parse → normalize → diff vs store.

### Outputs from the store
- **REST calls:** one per per-entity diff (POST/PUT/DELETE). Batched by type, serialized per UUID.
- **Disk writes:** only when the in-memory store diverges from disk (startup reconcile, inbound WS events, exit flush).

### The gatekeeper property
> Every write to disk originates in the store, and every read from disk goes *through* the store's diff engine. A write FROM the store back to disk never produces an outbound REST call because when the watchdog re-reads that file, the parsed+normalized result equals the store's current entry.

This obsoletes the `writeback_paused` flag and the 0.2s sleep bandaid at cli.py:1965–1972 and 2385–2387.

---

## The In-Memory Store

Lives on `_WatchState` as `state.store: EntityStore`.

```python
# api/bifrost/watch_store.py  (new)
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

@dataclass
class CanonicalEntry:
    """Single entity as held in the store."""
    entity_type: str           # "workflows", "forms", ...
    entity_id: str             # UUID
    dump: dict[str, Any]       # normalized JSON dict (byte-stable)
    model: Any                 # Pydantic instance (e.g. ManifestWorkflow)
    source: str                # "server" | "disk" | "in-flight"
    in_flight_ticket: int | None = None  # monotonic ticket for in-flight PUT

    @property
    def hash(self) -> str:
        import json
        return sha256(
            json.dumps(self.dump, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


@dataclass
class EntityStore:
    # entity_type → { uuid: CanonicalEntry }
    entries: dict[str, dict[str, CanonicalEntry]] = field(default_factory=dict)
    # Monotonic counter for in-flight ticket IDs
    _ticket_counter: int = 0
    # per-(type, id) asyncio.Lock for serializing concurrent writes for the same UUID
    _locks: dict[tuple[str, str], Any] = field(default_factory=dict)
```

**Why per-entity-type dicts keyed by UUID?** Matches the existing `Manifest` shape (cli.py imports `MANIFEST_FILES`). Diffs are cheap set/dict operations. `organizations` and `roles` are stored as dicts internally even though they serialize as YAML lists — the store handles that translation.

**Why keep both `dump` and `model`?** `dump` is what we diff and what we send over the wire. `model` is what we use for rendering back to YAML on inbound events (so we preserve Pydantic's field ordering/aliases).

**Why `in_flight_ticket`?** See "Concurrency / ordering" below. Prevents race where a slow PUT response mutates the store after a newer write has been queued.

---

## Normalization Layer

Two inputs that are semantically equal must produce byte-identical `dump` dicts and byte-identical `hash` values. The function:

```python
def canonicalize(entity_type: str, raw: dict) -> tuple[Any, dict]:
    """
    Parse raw dict (from YAML or WebSocket) into a Pydantic model,
    then dump it back to a canonical JSON dict.
    Returns (model, normalized_dump).
    """
    model_cls = MANIFEST_MODELS[entity_type]   # maps to ManifestWorkflow etc.
    model = model_cls.model_validate(raw)
    dump = model.model_dump(
        mode="json",
        exclude_defaults=True,
        by_alias=True,
    )
    return model, dump
```

**Why `exclude_defaults=True`?** A missing field in YAML and a field explicitly set to its default (e.g. `access_level: authenticated`) must compare equal.

**Why `by_alias=True`?** `ManifestTable.table_schema` serializes as `schema` (alias) — users edit YAML with `schema:`, but the model attribute is `table_schema`. Must roundtrip identically.

**Ordered lists with `position`:** `ManifestIntegrationConfigSchema` has a `position` field. After normalization, we sort each integration's `config_schema` list by `position` before comparing/serializing. Same for any other sequence with a position field.

**Secret fields:** integrations have OAuth `client_secret` that is never serialized. The YAML never carries it, the WS event never carries it, the PUT payload never carries it — there's nothing special to do, just rely on the Pydantic model's exclusion.

**Nested objects:** Integration `config_schema`, `mappings`, `oauth_provider`, and event-source `subscriptions` are all nested inside their parent's dump. We diff at the parent level; a nested change is observed as a parent change and produces a single PUT to the parent endpoint. This matches how the existing REST endpoints work (e.g. `PUT /api/integrations/{id}` accepts the full body including `config_schema`).

---

## Diff Engine

```python
def diff_entry(prev: CanonicalEntry | None, curr: CanonicalEntry | None) -> Change | None:
    if prev is None and curr is None:
        return None
    if prev is None:
        return Change(action="add", entity_type=curr.entity_type, entity_id=curr.entity_id, new=curr)
    if curr is None:
        return Change(action="delete", entity_type=prev.entity_type, entity_id=prev.entity_id, old=prev)
    if prev.hash == curr.hash:
        return None
    return Change(action="update", entity_type=curr.entity_type, entity_id=curr.entity_id, old=prev, new=curr)
```

Diffing a whole manifest against the store:

```python
def diff_manifest(store: EntityStore, parsed: Manifest) -> list[Change]:
    changes: list[Change] = []
    for entity_type in MANIFEST_FILES:
        store_section = store.entries.get(entity_type, {})
        parsed_section = _as_uuid_dict(parsed, entity_type)  # list→dict for orgs/roles
        for uuid in set(store_section) | set(parsed_section):
            prev = store_section.get(uuid)
            curr_raw = parsed_section.get(uuid)
            curr = _canonical_entry(entity_type, curr_raw) if curr_raw else None
            change = diff_entry(prev, curr)
            if change:
                changes.append(change)
    return changes
```

**Multi-line edit → one PUT.** `_process_watch_batch` collects the whole batch of changed files, parses the `.bifrost/` directory once, produces one store-diff, and emits N per-entity REST calls.

**Multi-entity edit → N PUTs.** If `workflows.yaml` has 5 workflows edited in the same save, the diff yields 5 changes. They're dispatched concurrently but serialized per-UUID via `store._locks`.

---

## Outbound Flow

```
file save
  → watchdog enqueues path
  → 300ms debounce
  → drain batch
  → if batch contains any .bifrost/*.yaml:
       parse .bifrost/ → Manifest
       normalize all entities
       diff vs store → changes = [Change, ...]
       for each change, concurrently:
           acquire store._locks[(type, id)]
           ticket = store.new_ticket()
           mark entry source="in-flight", in_flight_ticket=ticket
           send REST call (POST/PUT/DELETE)
           if success:
               update store with response body (re-normalize)
               mark source="disk"
           if failure:
               see "Failure handling"
  → non-.bifrost files (apps/, workflows/*.py, forms/*.yaml):
       push via existing /api/files/write path (unchanged)
```

**Outbound does NOT write disk.** The user already has the file on disk. After a successful REST call, we update only the store's `dump` — never re-serialize YAML back to the file. This is the critical departure from today's behavior (where the server's response can overwrite user edits).

**Echo suppression.** Each outbound REST call includes `X-Bifrost-Watch-Session: {state.session_id}`. The server's `entity_change_hook` already threads `session_id` into the broadcast, and `_ws_listener` already filters events where `event.get("session_id") == state.session_id` (cli.py:2092). No new mechanism needed.

---

## Inbound Flow

```
WebSocket entity_change event arrives
  → filter: event.session_id == state.session_id ? drop
  → decode event.data → raw dict
  → normalize → CanonicalEntry
  → diff vs store:
       if no change (store already has matching hash, e.g. we just pushed):
           drop silently
       else:
           update store
           write affected .bifrost/*.yaml
             (re-serialize the WHOLE section for that entity_type
              from the store, using Pydantic model_dump — matches
              the format `serialize_manifest_dir` produces)
           record written_paths so watchdog ignores the self-write
```

**Why re-serialize the whole section file?** The .yaml file contains N entities under one top-level key (`workflows: {uuid: {...}}`). Rewriting the whole file from the store guarantees consistent formatting. The written_paths short-circuit prevents the watchdog from treating our own write as a new edit — same mechanism used today.

**Delete action:** WebSocket event with `action=delete` and no `data` → remove entry from store → rewrite section file without that UUID → if the section becomes empty, delete the file (matching `serialize_manifest_dir` semantics).

**List-type sections (organizations, roles):** Store uses dict keyed by UUID internally; serialize as YAML list preserving sort order (by id, same as today's code).

---

## Startup Reconcile

```
on `bifrost watch` start:
  1. GET /api/files/manifest  → authoritative server state
  2. parse into Manifest
  3. normalize each entity → seed store with source="server"
  4. read .bifrost/ from disk → parse → Manifest(disk)
  5. diff_manifest(store, disk):
       each "add" change (in disk, not in store):
           → this is drift: treat as outbound, POST to server
       each "update" change (different dumps):
           → this is drift: treat as outbound, PUT to server
       each "delete" change (in store, not on disk):
           → AMBIGUOUS (disk is missing this entity)
           → conservative: WRITE the server's copy to disk, don't delete on server
           → log: "Restored {type}/{id} from server — was missing locally"
  6. now start filesystem watcher and WebSocket listener
```

**Rationale for conservative "delete" handling on startup:** if someone ran `rm .bifrost/events.yaml` by accident, we'd rather re-sync from server than cascade-delete entities. Explicit deletes go through `bifrost sync` TUI where the user confirms.

**No server-side changes needed for the GET.** `/api/files/manifest` already exists and is what `bifrost pull` uses.

---

## Exit Flow

On `KeyboardInterrupt` / `asyncio.CancelledError`:

```
1. Cancel the watchdog observer.
2. Close the WebSocket listener.
3. Final drain: process any pending outbound changes still in the queue.
4. Final flush-to-disk: for each store entry whose current dump doesn't
   match the on-disk value, rewrite the file. (Handles the case where
   inbound WS events arrived faster than we could write disk.)
5. De-register watch session: POST /api/files/watch {action: "stop"}.
```

---

## Mapping Table: entity type → REST endpoints

All entity types already have the endpoints the watcher needs. The watcher consults a routing table to pick the right URL + HTTP method per diff action. **Methods are shown as verified against the current code (PATCH is widely used for updates; a few use PUT).**

| Manifest key      | Add                                      | Update                                           | Delete                                       | Notes |
|-------------------|------------------------------------------|--------------------------------------------------|----------------------------------------------|-------|
| `workflows`       | **N/A** — file-based, use `/api/files/write` | **N/A** for code — see "Workflow metadata" below | `DELETE /api/workflows/{id}` | Source of truth is `workflows/*.py`. YAML `.bifrost/workflows.yaml` is generated. **Watcher treats `workflows.yaml` as read-only for code-derived fields** (name, function_name, path). Metadata fields (roles, access_level, etc.) use existing role/metadata endpoints — see workflow metadata note below. |
| `forms`           | `POST /api/forms`                        | `PATCH /api/forms/{id}`                          | `DELETE /api/forms/{id}`                     | |
| `agents`          | `POST /api/agents`                       | `PATCH /api/agents/{id}`                         | `DELETE /api/agents/{id}`                    | |
| `apps`            | `POST /api/applications`                 | `PATCH /api/applications/{id}/draft`             | `DELETE /api/applications/{id}`              | Watch touches draft only; publishing remains a UI action. |
| `integrations`    | `POST /api/integrations`                 | `PATCH /api/integrations/{id}`                   | `DELETE /api/integrations/{id}`              | Nested `config_schema`, `mappings`, `oauth_provider` may need separate sub-endpoints — **confirm in Task 0 audit**. |
| `configs`         | `POST /api/config`                       | `PATCH /api/config/{id}`                         | `DELETE /api/config/{id}`                    | Secret-type values are null in manifest; store skips diff when both sides are null. |
| `tables`          | `POST /api/tables`                       | `PATCH /api/tables/{id}`                         | `DELETE /api/tables/{id}`                    | |
| `events`          | `POST /api/events/sources`               | `PATCH /api/events/sources/{id}`                 | `DELETE /api/events/sources/{id}`            | Subscriptions have their own `POST`/`PATCH`/`DELETE` on `/api/events/sources/{id}/subscriptions[/{sub_id}]`. |
| `organizations`   | `POST /api/organizations`                | `PATCH /api/organizations/{org_id}`              | `DELETE /api/organizations/{org_id}`         | |
| `roles`           | `POST /api/roles`                        | `PATCH /api/roles/{id}`                          | `DELETE /api/roles/{id}`                     | |

### What the watcher sends in an update body

The watcher sends **only the fields that changed** between the store's previous snapshot and the new disk state — not the full canonicalized dump. This matches how PATCH handlers are implemented (every field in the `*Update` DTO is `Optional[T] = None`, and handlers apply changes conditionally: `if request.name is not None: org.name = request.name`). Sending only changed fields prevents accidental overwrites of fields the user didn't touch.

### Workflow metadata

`workflows.yaml` carries some fields whose source-of-truth is the DB row, not the `.py` source: `roles`, `access_level`, `organization_id`, `endpoint_enabled`, `timeout_seconds`, `public_endpoint`, `description`, `category`, `tags`. Most should be editable from YAML. Existing endpoints (to verify in Task 0):

- Roles → existing `POST /api/workflows/{id}/roles` pattern if it exists; otherwise audit output decides.
- Other metadata → needs to be confirmed: is there a `PATCH /api/workflows/{id}` that accepts these, or is a small additive endpoint needed?

Code-derived fields (`name`, `function_name`, `path`) are ignored by the watcher with a warning — those come from the `.py` file and the decorator-regenerated row.

---

## Echo Suppression & Edge Cases

### Session-based filter
Outbound calls send `X-Bifrost-Watch-Session: {session_id}`. The server threads this into `publish_file_activity` (existing behavior). The WS listener drops events whose `session_id` matches. Unchanged from today.

### Pending-PUT + local edit collision
> *What if the user edits a file while a PUT is in flight for the same entity?*

The store's `_locks[(type, id)]` serializes outbound mutations per UUID. The filesystem-change handler also acquires the same lock before processing. Ordering:

- T0: user saves file, mutation A queued.
- T1: PUT A starts; lock held; store entry marked `source="in-flight"`, ticket=1.
- T2: user saves again, mutation B queued; blocked on lock.
- T3: PUT A returns; store updated to match server response; lock released.
- T4: Mutation B acquires lock; **re-diff against current store state**; if new dump still differs, PUT B is sent; else drop.

This prevents "stale push" where B is sent based on a pre-A view of the world.

### In-flight PUT + inbound WS event for same entity
> *What if PUT A is in flight and a WS event (from another user) arrives for the same UUID?*

WS handler also acquires `_locks[(type, id)]`. It waits for PUT A to complete. Then it diffs against the now-updated store; if the WS payload is already what the store has (our PUT A), drop. Otherwise apply normally.

### Rapid-fire saves (3 saves in 100ms)
Debounce at 300ms so only the last content is read. Even without debounce, the lock-guarded re-diff above collapses duplicates.

### Delete-then-recreate with same UUID
Treated as two changes: DELETE then POST. The POST includes the UUID in the body (all our entity Create DTOs accept a client-supplied UUID — verified for forms, agents, integrations; need to confirm/enable for events and tables). If UUID-on-create isn't supported for some type, we'd need to defer the recreate and let the server assign a new UUID, then update the store + rewrite the YAML. This edge case is rare enough to leave as a follow-up.

---

## Concurrency / Ordering

- **Per-UUID serialization:** `store._locks[(type, id)]` — an `asyncio.Lock` acquired around any mutation touching that entity. Ensures at most one outbound call per UUID at a time.
- **Cross-entity concurrency:** different UUIDs can PUT in parallel (`asyncio.gather`).
- **Coalescing:** debounce filesystem events at 300ms so a burst of saves produces one diff pass.
- **Retry ordering:** if a PUT fails transiently, re-queue the Change object; the lock ensures it won't race with newer edits (re-diff on retry pulls current disk state).
- **Tickets:** `in_flight_ticket` is monotonic per-store. A PUT response is only applied if its ticket matches the entry's current `in_flight_ticket`. Stale responses (from a retried request) are discarded.

---

## Failure Handling

| Status | Meaning | Action |
|--------|---------|--------|
| `200/201/204` | Success | Update store with response body (or with the sent dump if 204). Mark `source="disk"`, clear ticket. |
| `404` (on PUT/DELETE) | Entity not on server (our store is stale) | Log warning. Re-fetch manifest, reseed affected section. Do NOT retry. |
| `409` (conflict) | Server-side version mismatch | GET the current server copy, show a warning row in TUI, do NOT overwrite. User resolves by re-editing. |
| `422` (validation) | Bad payload | Surface error in TUI with details. Keep entry as `source="in-flight-failed"` — no further retries until user edits again. |
| `5xx` | Server error | Retry with exponential backoff (1s, 2s, 4s, max 30s). After 5 attempts, surface error and give up for that change. |
| Network error | Offline / disconnected | Requeue forever; backoff capped at 30s. WS listener handles reconnect separately. |

**TUI surfacing:** the existing `watch_app.log_error` / `log_warning_detail` methods cover this. Add a new "Push failed — will retry" state with a spinner until success/abandoned.

**Store rollback:** on terminal failure, the store entry keeps its last-successful dump. The disk still has the user's edit. The next save re-triggers the diff and retries. No auto-revert of user's file.

---

## Migration Strategy

The new path is a **full replacement** for watch mode, not a feature flag. Rationale:

- The current watch flow is actively buggy (silent deletions).
- Behind-the-scenes, it already shares infrastructure (`_WatchState`, session_id, WS listener, watchdog) that the new flow extends.
- The CLI ships as a pip package; users get the new version the next time they `pip install --force-reinstall`.

**Bulk manifest import remains** for:
- `bifrost sync` (interactive TUI, explicit user consent for deletions) — unchanged.
- Future `bifrost import` — added in a follow-up plan.
- `/api/files/manifest/import` endpoint — unchanged (still used by `bifrost sync` and by the UI's import flow).

**Coexistence.** During the rollout window, an older CLI watching the same workspace as a newer CLI would compete — older would push bulk, newer would push per-entity. This is no worse than today. We document "upgrade all clients to avoid mixed-mode races" in the CLI release notes.

---

## What the new `_process_watch_batch` looks like

```python
async def _process_watch_batch(
    client: BifrostClient,
    changes: set[str],
    deletes: set[str],
    base_path: pathlib.Path,
    repo_prefix: str,
    state: _WatchState,
    watch_app: WatchApp | None = None,
) -> None:
    """Process a batch of file changes and deletions."""

    # 1. Non-manifest files: use existing per-file write/delete path.
    bifrost_changes = {p for p in changes if _is_bifrost_manifest_path(p, base_path)}
    regular_changes = changes - bifrost_changes
    bifrost_deletes = {p for p in deletes if _is_bifrost_manifest_path(p, base_path)}
    regular_deletes = deletes - bifrost_deletes

    await _push_regular_files(client, regular_changes, regular_deletes, base_path, repo_prefix, state, watch_app)

    # 2. Manifest files: diff the whole .bifrost/ dir against the store.
    if bifrost_changes or bifrost_deletes:
        bifrost_dir = _find_bifrost_dir(base_path)
        disk_manifest = read_manifest_from_dir(bifrost_dir)
        store_changes = state.store.diff_against(disk_manifest)

        if not store_changes:
            return  # No structural change (e.g. just whitespace)

        # 3. Validate cross-references BEFORE dispatching.
        errors = validate_manifest(disk_manifest)
        if errors:
            for err in errors:
                _log_error(watch_app, f"Manifest validation: {err}")
            return  # Refuse to push invalid state.

        # 4. Dispatch per-entity REST calls.
        await state.store.apply_changes(
            client, store_changes, session_id=state.session_id, watch_app=watch_app,
        )

    # 5. Auto-validate app directories (unchanged).
    ...
```

`EntityStore.apply_changes` is where the per-type routing table lives:

```python
async def apply_changes(self, client, changes, *, session_id, watch_app):
    headers = {"X-Bifrost-Watch-Session": session_id}
    async def dispatch(change):
        async with self.lock_for(change.entity_type, change.entity_id):
            # Re-diff against current store state (coalesce concurrent edits)
            current = self.entries.get(change.entity_type, {}).get(change.entity_id)
            desired = change.new  # None for delete
            if _hash_equal(current, desired):
                return  # Already applied (e.g. by a concurrent PUT)

            ticket = self.new_ticket()
            self._mark_in_flight(change, ticket)
            try:
                response = await _dispatch_rest(client, change, headers)
                self._apply_response(change, response, ticket)
                _log_push(watch_app, change)
            except HTTPStatusError as e:
                self._handle_rest_error(change, e, watch_app)

    await asyncio.gather(*(dispatch(c) for c in changes), return_exceptions=True)
```

`_dispatch_rest` is the mapping-table lookup.

---

## Tests Needed

### Unit tests (`api/tests/unit/`)

- `test_watch_canonicalize.py` — normalization round-trips:
  - Missing field vs default field → equal.
  - Alias field (`schema` ↔ `table_schema`) → equal.
  - List reordering via `position` → equal.
  - Null secret fields → equal.
  - Whitespace, key-order variants of same YAML → equal.
- `test_watch_diff_engine.py`:
  - Empty store vs empty manifest → no changes.
  - Add: new UUID → one `add` change.
  - Delete: UUID in store, not in manifest → one `delete` change.
  - Update: same UUID, different dump → one `update` change.
  - Nested subscription change in event source → update at parent level.
  - Integration config_schema reorder (with explicit position) → no change.
- `test_watch_store.py`:
  - `apply_changes` serializes per UUID via lock.
  - In-flight ticket mismatch drops stale response.
  - 404 on PUT re-fetches manifest.
  - 409 on PUT surfaces conflict without overwriting disk.
  - 5xx retries with backoff.

### E2E tests (`api/tests/e2e/platform/test_watch_gatekeeper.py` — new)

- **The disappearing-entity regression:**
  1. Spin up CLI watch session.
  2. Concurrently: POST a new event source via the API.
  3. Concurrently: touch an unrelated workflow `.py` file in the workspace.
  4. Assert: watch session receives the entity_change event, writes the UUID to `events.yaml`.
  5. Assert: the event source still exists in the DB after the workflow push.
- **UI-create then watcher sees it:**
  1. Start watch. Assert startup reconcile populates store from server.
  2. Via API, POST a new form.
  3. Assert `forms.yaml` on disk contains the new UUID within 2s.
- **Disk edit → PUT round-trip:**
  1. Start watch.
  2. Edit a form's name in `forms.yaml`.
  3. Assert `PUT /api/forms/{id}` was called with the new name (header `X-Bifrost-Watch-Session` present).
  4. Assert the server's entity_change WS event (with same session_id) is filtered out — no duplicate disk write.
- **Disk delete of an entity:**
  1. Start watch.
  2. Remove an entry from `agents.yaml`.
  3. Assert `DELETE /api/agents/{id}` was called.
- **Two CLIs, same workspace, same entity:**
  1. CLI A edits form X.
  2. CLI B (different session) should receive the WS event and write X to its local `forms.yaml` without echoing a PUT back.
- **Offline/retry:**
  1. Stop the API server mid-edit.
  2. Edit a form. Assert the change is queued.
  3. Restart API. Assert the PUT eventually succeeds.

### Manual smoke tests
- `bifrost watch` in a workspace with existing `.bifrost/`. Edit each entity type: workflows (.py), forms, agents, apps, integrations, configs, tables, events, organizations, roles. Verify each produces the expected REST call.
- Kill watch mid-edit; restart; verify startup reconcile picks up drift.

---

## Implementation Tasks

### Task 0: Compatibility Audit (MUST COMPLETE BEFORE TASK 1)

**Why this exists.** The `ManifestXxx` Pydantic models (what's in the YAML) and the `XxxUpdate` / `XxxCreate` DTOs (what the existing REST endpoints accept) were designed independently. The old bulk manifest import bridged them via the `_resolve_*` methods in `manifest_import.py` / `github_sync.py`, which did field translation, nested upsert patterns (e.g. integration config_schema), and UI-managed-field preservation. Switching to per-entity REST means we lose that translation layer. Before committing to the redesign, we verify it's actually workable.

**For each entity type** (`organizations`, `roles`, `workflows`, `forms`, `agents`, `apps`, `integrations`, `configs`, `tables`, `events`, event subscriptions), produce a short compatibility report answering:

1. **Field parity.** Does every field on `ManifestXxx` have a corresponding writable field on the `XxxUpdate` / `XxxCreate` DTO? If not, which fields are manifest-only or DTO-only?
2. **Aliases and casing.** Any field name differences (e.g. `ManifestTable.table_schema` serializes as `schema`)? Does the DTO use the same aliases?
3. **Nested entities.** For entities with nested collections (integration `config_schema`, integration `mappings`, event `subscriptions`), can the parent PATCH update them in one call, or do we need separate endpoints per child?
4. **UI-managed fields.** Which fields on the manifest model are set by the UI (not the YAML) — `oauth_token_id` on mappings, config `value` for user-set configs, OAuth `client_id`, etc.? Does PATCH round-trip them cleanly, or will the watcher need to ignore them?
5. **UUID-on-create.** Does POST accept a client-supplied UUID, or does the server assign one? Needed for delete-then-recreate-same-UUID scenarios.
6. **Translation rules in the old `_resolve_*` methods.** Skim each one and list any non-obvious behavior (e.g. `_resolve_integration`'s non-destructive upsert of config_schema that preserves FK-linked Config rows). Could replicating those rules client-side in the watcher be avoided or is it required?

**Deliverable.** A markdown doc at `docs/plans/2026-04-16-manifest-compat-audit.md` (sibling to this plan) with a table per entity type.

### Decision Gate at the end of Task 0

After the audit, categorize each entity type as one of:

- **Green** — Manifest ↔ DTO map cleanly, no translation gymnastics. Watcher routes directly through existing PATCH/POST/DELETE.
- **Yellow** — Minor gaps (a field or two, small aliases). Resolved by either: (a) extending the DTO with the missing fields, or (b) the watcher ignoring those fields in the YAML. Document which.
- **Red** — Structural mismatch that would require either significant REST-handler work OR maintaining two parallel models (`ManifestXxx` for YAML + `XxxUpdate` for API) just to mirror one into the other.

**If most entity types are Green/Yellow:** proceed with Task 1.

**If multiple entity types are Red:** **pause this plan and open a scoping conversation.** The alternative direction is:

> Don't try to keep `.bifrost/` YAML as the watch interface at all. Watch only syncs non-manifest files (apps/, workflows/*.py, forms/*.yaml — the code/content files). All entity mutations happen through `bifrost api` (or an equivalent CLI command) that calls the existing REST endpoints directly with user-supplied args. `.bifrost/` becomes an export/import artifact managed by `bifrost export` / `bifrost import`, not something watch mode touches.

The choice between "in-memory gatekeeper for `.bifrost/` watch" and "pivot to `bifrost api`" depends entirely on how much translation work Task 0 reveals. If the `ManifestXxx` ↔ `XxxUpdate` pairs are mostly compatible, the gatekeeper is the lower-friction path. If they're not, it's less work to just retire `.bifrost/` from the watch path than to maintain two model-translation layers (client-side in the watcher + server-side in the old `_resolve_*`) indefinitely.

**Commit:** `docs(plans): add manifest ↔ REST DTO compatibility audit`

### Task 1: Create `watch_store.py` with normalization + diff engine

**Files:**
- Create: `api/bifrost/watch_store.py` (`CanonicalEntry`, `EntityStore`, `canonicalize`, `diff_entry`, `diff_manifest`, `MANIFEST_MODELS` lookup).
- Test: `api/tests/unit/test_watch_canonicalize.py`, `api/tests/unit/test_watch_diff_engine.py`.

No CLI wiring yet. Pure functions + container class.

**Commit:** `feat(watch): add in-memory entity store with canonical normalization and diff`

### Task 2: (Removed — endpoints already exist; any small gaps surfaced in Task 0 are handled under the entity type they affect.)

### Task 3: Wire store into `_WatchState` + startup reconcile

**Files:**
- Modify: `api/bifrost/cli.py`:
  - `_WatchState.__init__` gains `self.store = EntityStore()`.
  - New `_seed_store_from_server(client, state)` called from `_watch_and_push` before the filesystem observer starts.
  - New `_reconcile_store_with_disk(state, base_path, client)` runs after seeding.

**Commit:** `feat(watch): seed in-memory store from server on startup, reconcile with disk`

### Task 4: Replace `_process_watch_batch` bulk manifest path with per-entity REST dispatch

**Files:**
- Modify: `api/bifrost/cli.py`:
  - Remove the `/api/files/manifest/import` call from `_process_watch_batch` (lines ~1892–1911).
  - Add `EntityStore.apply_changes(client, changes, session_id, watch_app)` with the routing table.
  - Remove the `state.writeback_paused = True` block (lines 1965–1972). No longer needed.
- Test: `api/tests/unit/test_watch_store.py` (apply_changes routing, locks, tickets).

**Commit:** `refactor(watch): dispatch per-entity REST calls instead of bulk manifest import`

### Task 5: Refactor `_process_incoming` to route WS entity_change through the store

**Files:**
- Modify: `api/bifrost/cli.py`:
  - `_process_incoming`'s entity_change block (lines 2204–2307) now: normalize → diff vs store → if change, update store and rewrite YAML section file from the store.
  - Remove the 0.2s sleep at line 2385.

**Commit:** `refactor(watch): route incoming entity_change events through in-memory store`

### Task 6: Handle workflow YAML specially (read-only; redirect metadata edits)

**Files:**
- Modify: `api/bifrost/cli.py`: when the diff produces a change under `workflows`, emit either a warning ("workflow source-of-truth is the .py file; ignoring") or dispatch to `PATCH /api/workflows/{id}/metadata` for the subset of fields that are truly metadata (roles, access_level, endpoint_enabled, etc.).
- Tests: E2E verifying that `workflows.yaml` `.py`-derived fields (name, function_name) are ignored on edit, but `roles` / `access_level` edits fire the metadata PATCH.

**Commit:** `feat(watch): handle workflows.yaml edits via metadata patch; ignore .py-derived fields`

### Task 7a: Simulation harness for rapid-change scenarios

**Why this exists.** The system has three concurrent input channels (filesystem, WebSocket, startup fetch) converging on one store. Correctness under rapid/interleaved changes is the whole point of the gatekeeper design. We need a test harness that can replay scripted scenarios and fuzz randomized event streams without real servers, real WebSockets, or real filesystems.

**Files:**
- Create: `api/tests/unit/watch_sim/__init__.py`, `api/tests/unit/watch_sim/fakes.py` (FakeRestClient, FakeFileSystem, FakeWebSocket), `api/tests/unit/watch_sim/runner.py` (Scenario, At, expect-DSL), `api/tests/unit/watch_sim/invariants.py`.
- Create: `api/tests/unit/test_watch_sim_scripted.py` — the named regression scenarios (list in "Testing" section below).
- Create: `api/tests/unit/test_watch_sim_fuzz.py` — randomized stress runs.

**Scripted scenarios to ship day-one:**
1. Disappearing-entity race (UI create races with local workflow save) → assert no DELETE fires.
2. Rapid local edits (5 saves in 100ms) → assert one PATCH with final content.
3. Offline retry (REST returns 4× network error then 200) → assert eventual success, no stale state.
4. Conflict (409) → assert disk not overwritten, warning surfaces, store reflects server after re-fetch.
5. WS event for entity we just PUT'd → assert dropped via store-hash match.
6. Two simultaneous saves to same UUID → assert per-UUID lock serializes them.
7. Delete-then-recreate same UUID → assert DELETE then POST with UUID in body.

**Invariants for fuzz runs (500+ events over simulated 30s):**
- No DELETE REST call for an entity_id that was never observed as absent from the store.
- Every surviving (non-own-session) WS event either mutates the store OR is dropped due to hash match.
- Every outbound PATCH body contains only fields whose store-vs-disk values differed at diff time.
- Final store state equals the simulated server's final ledger state.

**Commit:** `test(watch): add simulation harness with scripted + fuzz scenarios for gatekeeper correctness`

### Task 7: E2E regression test for disappearing entity

**Files:**
- Create: `api/tests/e2e/platform/test_watch_gatekeeper.py` — the full scenario list in "Tests Needed".

**Commit:** `test(watch): add E2E coverage for in-memory gatekeeper including disappearing-entity regression`

### Task 8: Remove the tactical `delete_removed_entities=False` workaround

Once Task 4 lands, the line that was flipped to `False` as the tactical fix becomes dead code (the call site is gone). Remove it and any related comments.

**Commit:** `chore(watch): remove tactical delete_removed_entities workaround now that bulk import is unused`

### Task 9: Docs + changelog

**Files:**
- Modify: `docs/cli.md` (or equivalent CLI reference) — document that `bifrost watch` now uses per-entity REST.
- Update `docs/plans/2026-03-11-incremental-manifest-import.md` status — mark deprioritized or relevant only to `bifrost sync`.

**Commit:** `docs: update watch mode description for in-memory gatekeeper redesign`

---

## Risks and Open Questions

### Risks

1. **Missing PUT endpoints broaden the blast radius.** Task 2 touches three routers we haven't had to change recently. Mitigation: add them behind careful superuser auth; tests mirror existing POST tests.
2. **Startup reconcile latency.** `GET /api/files/manifest` is ~1–3s for large workspaces. During that window, filesystem events are queued but not processed. Mitigation: start the observer in "paused" mode, buffer events, flush after reconcile.
3. **Workflow metadata endpoint scope-creep.** `PATCH /api/workflows/{id}/metadata` needs a clear, minimal contract — don't let it sprawl into a full PUT.
4. **Debounce timing.** 300ms is a guess. Too short → multiple REST calls per save; too long → feels laggy. Tune based on user feedback.
5. **Diff engine correctness vs Pydantic defaults.** If a model adds a new optional field with a default, existing on-disk YAML suddenly diffs against newly-seeded server state. Mitigation: canonicalization runs both sides through the same Pydantic; defaults apply symmetrically. But cross-version skew (old CLI, new server) could produce spurious diffs.

### Open questions

1. **UUID-on-create support.** Do `POST /api/events/sources` and `POST /api/tables` accept client-supplied UUIDs? If not, delete-then-recreate with the same UUID fails. **Action:** verify during Task 4; file follow-up if gaps exist.
2. **Should `bifrost watch` refuse to start if the workspace has any orphan files (not in `.bifrost/`)?** Current behavior: we don't touch those. Stay the course — watch is narrowly scoped to manifest entities and the existing per-file path.
3. **What happens if a user manually runs `bifrost push --mirror` while `bifrost watch` is active?** They probably shouldn't, but today it's allowed. Document "don't do both concurrently."
4. **Do we need a `bifrost watch --dry-run` mode?** Helpful for debugging diffs before pushing. Could be a low-effort follow-up.
5. **WebSocket reconnect during a pending-retry storm.** If the socket reconnects, the server may replay events. The store's hash-equality check drops duplicates, so this should be fine — but worth a test.
6. **Large manifest performance.** 1000+ entities: startup reconcile does 1000+ hash comparisons. O(N) is fine; confirm end-to-end under load.

---

## Follow-ups (future plans)

- **`bifrost export` / `bifrost import`** — the explicit artifact-movement commands that replace `POST /api/files/manifest/import` for cross-instance moves. Watch stops touching `.bifrost/` entirely; `.bifrost/` becomes an export-format artifact.
- **`bifrost api`** (or successor) becomes the interactive session for entity mutations from the CLI, using the same per-entity REST endpoints.
- **Shared store implementation between CLI watch and a prospective desktop app** — if we ever ship a GUI Bifrost client, the `EntityStore` is the natural place to reuse.

---

### Critical Files for Implementation

- `/home/jack/GitHub/bifrost/api/bifrost/cli.py` — `_WatchState`, `_process_watch_batch`, `_process_incoming`, `_watch_and_push`, `_ws_listener`; this is where the gatekeeper wires in.
- `/home/jack/GitHub/bifrost/api/bifrost/manifest.py` — `MANIFEST_FILES`, Pydantic models, `parse_manifest_dir`, `serialize_manifest_dir`; foundation for normalization and serialization.
- `/home/jack/GitHub/bifrost/api/bifrost/watch_store.py` — **new**: `EntityStore`, `CanonicalEntry`, `canonicalize`, `diff_entry`, `diff_manifest`, `apply_changes`.
- `/home/jack/GitHub/bifrost/api/src/routers/organizations.py`, `/home/jack/GitHub/bifrost/api/src/routers/tables.py`, `/home/jack/GitHub/bifrost/api/src/routers/events.py`, `/home/jack/GitHub/bifrost/api/src/routers/workflows.py` — add the missing PUT/PATCH endpoints.
- `/home/jack/GitHub/bifrost/api/src/core/entity_change_hook.py` — already broadcasts `entity_change` with `session_id`; reference for the inbound contract (no code changes expected).
