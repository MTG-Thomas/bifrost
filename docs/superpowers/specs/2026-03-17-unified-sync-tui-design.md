# Unified Sync TUI Design

## Context

The Bifrost CLI currently has separate `push` and `pull` flows with separate TUI apps (`FileSelectApp`, `EntityReviewApp`). This creates several UX problems:

1. **No automatic pull before watch** — `bifrost watch` starts with `_push_files()` only, missing server changes made while offline
2. **Fixed column widths** — File column hardcoded at 40 chars, doesn't use available terminal width
3. **Confusing "delete" semantics** — Files on server but not locally show as "delete" with the server author's name, making it look like you're deleting someone else's work rather than acknowledging the file doesn't exist locally
4. **Separate entity/file apps** — Two sequential fullscreen TUIs is jarring; entities shown as a table behind the app
5. **No bidirectional entity sync** — Entities can only be pushed, not pulled from platform

**Note:** A previous `bifrost sync` command existed for git-level sync and was removed in favor of `bifrost git push`. This design reintroduces `bifrost sync` with different semantics — file/entity-level bidirectional sync, not git operations.

## Design

### New `bifrost sync` command

A single `bifrost sync` command replaces the separate push/pull flows. `bifrost push` and `bifrost pull` become aliases that call the same unified sync code. `bifrost watch` calls sync at startup.

`bifrost sync` accepts the same arguments as the current push/pull:
- `path` — workspace directory (positional, optional)
- `--force` — skip TUI, use defaults
- `--mirror` — include server-only files (for deletion or pull)
- `--validate` — run manifest validation before sync

### Unified SyncApp TUI

One Textual app replaces `FileSelectApp` and `EntityReviewApp`. It shows both files and entities in a single scrollable view with section headers.

**Widget strategy:** Custom `ListView` with `ListItem` subclass (`SyncRow`). Each `SyncRow` holds its item data and current action state. This replaces `SelectionList` (checkbox-based) since the interaction model is per-row action cycling, not checkbox toggling.

**Layout:**

```
╭─ bifrost sync ── Scanned 47 files, 38 unchanged ────────────────────────────────────────╮
│                                                                                          │
│  Item                                       Action      Why           Modified   Author  │
│  ────────────────────────────────────────────────────────────────────────────────────────  │
│                                                                                          │
│  ── Files ──────────────────────────────────────────────────────────────────────────────  │
│  ▸ workflows/billing_automation.py          ↑ Push      local newer   10:05              │
│    workflows/onboarding_flow.py             ↑ Push      new locally   09:45              │
│    features/ticketing/create_ticket.py      ↓ Pull      server newer  14:22    michael   │
│    apps/dashboard/src/Sidebar.tsx           ✕ Delete    server only   09:00    michael   │
│                                                                                          │
│  ── Entities ───────────────────────────────────────────────────────────────────────────  │
│    Integration: salesforce                  ↑ Push      local changed                    │
│    Config: default_timeout                  ↑ Push      new locally                      │
│    Workflow: old_billing                    ↓ Pull      platform only                    │
│                                                                                          │
│  4 files · 3 entities · ↑↓ navigate · ←→ cycle action · Enter confirm · Esc cancel     │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

The focused row is indicated by `▸` prefix and a highlight bar (Textual's built-in ListView highlighting).

### Dynamic column widths

The File/Item column expands to fill available terminal width. Fixed-width columns:
- Action: 10 chars
- Why: 14 chars
- Modified: 10 chars
- Author: 20 chars (or computed from max data length, minimum 10)

File column = `terminal_width - fixed_columns - padding`. Minimum 30 chars.

Entity rows use the same column layout. Modified and Author columns are blank for entities (entity diff data doesn't include timestamps or author info).

### Sync state detection

File sync state is determined by comparing local files against server metadata:

| Condition | State | How detected |
|---|---|---|
| File exists locally, not on server | `new locally` | `repo_path not in server_metadata` |
| File exists on server, not locally | `server only` | `server_path not in local_files` (requires `--mirror`) |
| Both exist, MD5 matches | unchanged (hidden) | `local_md5 == server_etag` |
| Both exist, MD5 differs, local mtime > server `last_modified` | `local newer` | timestamp comparison |
| Both exist, MD5 differs, server `last_modified` > local mtime | `server newer` | timestamp comparison |

**V1 simplification:** We do NOT implement true "both changed" conflict detection (which would require tracking last-sync state). If both exist and differ, we compare timestamps — whichever is newer gets the default. In practice, the user can always override via action cycling. True conflict detection with a sync state file can be added later if needed.

### Cycleable action per item

Each row has a context-aware action that cycles with left/right arrow keys (or h/l). The default action is always the sensible one. Colors: Push = green (`↑`), Pull = blue (`↓`), Delete = red (`✕`), Skip = dim (`·`).

**File action cycles:**

| File State | Default | Cycle Options |
|---|---|---|
| New locally | ↑ Push | → ✕ Delete → · Skip → ↑ Push |
| Server only | ↓ Pull | → ✕ Delete → · Skip → ↓ Pull |
| Local newer | ↑ Push | → ↓ Pull → · Skip → ↑ Push |
| Server newer | ↓ Pull | → ↑ Push → · Skip → ↓ Pull |

**Entity action cycles:**

| Entity State | Default | Cycle Options |
|---|---|---|
| Local changed | ↑ Push | → ↓ Pull → · Skip → ↑ Push |
| New locally | ↑ Push | → ✕ Delete → · Skip → ↑ Push |
| Platform only | ↓ Pull | → ✕ Delete → · Skip → ↓ Pull |

### "Why" column replaces "Status"

Instead of "new" / "changed" / "delete", the column explains the sync state:
- `new locally` — file/entity exists only in local workspace
- `server only` / `platform only` — exists only on server/platform
- `local newer` — both exist, local version is newer (by timestamp)
- `server newer` — both exist, server version is newer (by timestamp)
- `local changed` — entity manifest differs from platform state

### Interaction model

- **↑/↓** (or j/k) — navigate between items (skips section headers)
- **←/→** (or h/l) — cycle action for focused item
- **Enter** — confirm and execute all actions
- **Esc** — cancel
- **a** — reset all to defaults
- **s** — skip all (set everything to Skip)
- Mouse click on action column cycles forward

### Entity pull mechanism

Entity "Pull" uses the existing `GET /api/files/manifest` endpoint which returns current platform state as serialized YAML files. The CLI writes the returned `.bifrost/*.yaml` files to the local workspace, overwriting local manifest changes with platform truth.

Entity "Push" uses the existing `POST /api/files/manifest/import` endpoint (current behavior).

Entity "Delete" uses the import endpoint with `delete_removed_entities=True` and the target entity excluded from the submitted manifest. To avoid accidentally deleting unrelated entities, the CLI constructs the manifest from the full local `.bifrost/` directory minus the entity to delete — so only that specific entity is removed.

### Command surface

| Command | Behavior |
|---|---|
| `bifrost sync [path]` | Full bidirectional sync TUI (new primary command) |
| `bifrost push [path]` | Alias for `bifrost sync` (backwards compat) |
| `bifrost pull [path]` | Alias for `bifrost sync` (backwards compat) |
| `bifrost watch [path]` | Calls `_sync_files()` at startup, then enters watch loop |

All commands accept `--force`, `--mirror`, and `--validate` flags.

### Key files to modify

| File | Changes |
|---|---|
| `api/bifrost/tui/sync_app.py` | **New.** Unified SyncApp with custom ListView/SyncRow widgets |
| `api/bifrost/cli.py` | New `_sync_files()` function. New `bifrost sync` command. Update watch startup. Wire push/pull as aliases. |
| `api/bifrost/tui/file_select.py` | Deprecated (kept temporarily, removed after migration) |
| `api/bifrost/tui/entity_review.py` | Deprecated (kept temporarily, removed after migration) |

### Existing code to reuse

- `_push_files()` in `cli.py` — file scanning, server metadata fetch, MD5 comparison, upload/delete logic
- `_process_incoming()` in `cli.py` — file download + local write pattern (from watch mode)
- `_entity_diff_pre_push()` in `cli.py` — dry-run entity diffing
- `GET /api/files/manifest` endpoint (`files.py:406`) — platform manifest for entity pull
- `POST /api/files/manifest/import` endpoint (`files.py:427`) — entity push
- `BifrostApp` base class from `tui/theme.py`
- `ProgressApp` from `tui/progress.py` — post-confirmation upload/download progress display
- Entity column width computation pattern from `EntityReviewApp`

## Verification

1. Run `bifrost sync` on a workspace with mixed local/server changes — verify unified TUI shows both files and entities
2. Cycle actions with arrow keys — verify correct options per state
3. Execute push actions — verify files uploaded to server
4. Execute pull actions — verify files downloaded locally
5. Execute entity pull — verify `.bifrost/*.yaml` updated from platform
6. Run `bifrost watch` — verify initial sync runs before watch loop
7. Run `bifrost push` / `bifrost pull` — verify they invoke sync
8. Run with `--force` — verify TUI skipped, defaults used
9. Run in non-TTY — verify fallback behavior (no TUI, use defaults)
10. Test with `--mirror` flag — verify server-only files appear
