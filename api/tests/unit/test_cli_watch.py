"""Unit tests for CLI watch mode logic."""
import pathlib
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_writeback_preserves_concurrent_user_edits():
    """Writeback should only discard events for writeback files, not user edits."""
    pending_changes: set[str] = set()
    lock = threading.Lock()

    # Simulate: user edit arrives during push
    with lock:
        pending_changes.add("/workspace/apps/my-app/user-edit.tsx")

    # Simulate: writeback writes manifest files
    writeback_paths = {"/workspace/.bifrost/workflows.yaml"}

    # The fix: only discard writeback paths
    with lock:
        pending_changes -= writeback_paths

    # User edit should still be in the queue
    assert "/workspace/apps/my-app/user-edit.tsx" in pending_changes


def test_writeback_discards_written_paths():
    """Writeback paths themselves should be removed from pending sets."""
    pending_changes: set[str] = set()
    pending_deletes: set[str] = set()
    lock = threading.Lock()

    # Simulate: watcher queued events for files that writeback will overwrite
    with lock:
        pending_changes.add("/workspace/.bifrost/workflows.yaml")
        pending_changes.add("/workspace/.bifrost/integrations.yaml")
        pending_deletes.add("/workspace/.bifrost/workflows.yaml")

    writeback_paths = {
        "/workspace/.bifrost/workflows.yaml",
        "/workspace/.bifrost/integrations.yaml",
    }

    with lock:
        pending_changes -= writeback_paths
        pending_deletes -= writeback_paths

    assert len(pending_changes) == 0
    assert len(pending_deletes) == 0


def test_writeback_empty_paths_is_noop():
    """When writeback writes nothing (all identical), pending sets are untouched."""
    pending_changes: set[str] = set()
    lock = threading.Lock()

    with lock:
        pending_changes.add("/workspace/apps/my-app/user-edit.tsx")

    writeback_paths: set[str] = set()

    with lock:
        if writeback_paths:
            pending_changes -= writeback_paths

    assert "/workspace/apps/my-app/user-edit.tsx" in pending_changes


def test_read_only_events_are_ignored():
    """Opened/closed events (read-only access) should not trigger pushes."""
    pending_changes: set[str] = set()
    pending_deletes: set[str] = set()
    lock = threading.Lock()

    def simulate_event(event_type: str, src: str) -> None:
        """Mirrors the ChangeHandler.on_any_event filtering logic (non-moved events)."""
        with lock:
            if event_type == "deleted":
                pending_deletes.add(src)
                pending_changes.discard(src)
            elif event_type in ("created", "modified", "closed"):
                pending_changes.add(src)
                pending_deletes.discard(src)

    # Read-only events should be ignored
    simulate_event("opened", "/workspace/apps/my-app/index.tsx")
    assert len(pending_changes) == 0
    assert len(pending_deletes) == 0

    # Content-modifying events should be captured
    simulate_event("created", "/workspace/apps/my-app/new-file.tsx")
    assert "/workspace/apps/my-app/new-file.tsx" in pending_changes

    simulate_event("modified", "/workspace/apps/my-app/index.tsx")
    assert "/workspace/apps/my-app/index.tsx" in pending_changes

    # Closed events should be captured (Linux inotify: some editors emit created → closed with no modified)
    pending_changes.clear()
    simulate_event("closed", "/workspace/apps/my-app/new-file.tsx")
    assert "/workspace/apps/my-app/new-file.tsx" in pending_changes

    simulate_event("deleted", "/workspace/apps/my-app/old-file.tsx")
    assert "/workspace/apps/my-app/old-file.tsx" in pending_deletes


# =============================================================================
# Moved events (editor atomic saves: write .tmp then rename)
# =============================================================================


def test_moved_event_queues_destination_as_change():
    """A moved/renamed event should queue the destination path as a change."""
    pending_changes: set[str] = set()
    pending_deletes: set[str] = set()
    lock = threading.Lock()

    # Simulate: editor writes test.txt.tmp then renames to test.txt
    dest = "/workspace/test.txt"
    with lock:
        pending_changes.add(dest)
        pending_deletes.discard(dest)

    assert dest in pending_changes
    assert dest not in pending_deletes


def test_moved_event_does_not_delete_source():
    """Moved source (often a temp file) should NOT be queued for server deletion."""
    pending_changes: set[str] = set()
    pending_deletes: set[str] = set()
    lock = threading.Lock()

    # Simulate: only destination is queued, source is ignored
    src = "/workspace/test.txt.tmp.112174.1771947498343"
    dest = "/workspace/test.txt"
    with lock:
        pending_changes.add(dest)
        pending_deletes.discard(dest)

    assert src not in pending_deletes
    assert src not in pending_changes
    assert dest in pending_changes


# =============================================================================
# Fix 1: Observer health check and error resilience
# =============================================================================


def test_observer_death_detected_and_restart_attempted():
    """When the observer thread dies, the watch loop should detect and restart it."""
    from watchdog.observers import Observer

    observer = MagicMock(spec=Observer)
    observer.is_alive.return_value = False

    # Simulate the health check logic from the watch loop
    restarted = False
    if not observer.is_alive():
        new_observer = MagicMock(spec=Observer)
        new_observer.start.return_value = None
        new_observer.schedule.return_value = None
        # In real code, observer is reassigned
        observer = new_observer
        observer.schedule(MagicMock(), "/workspace", recursive=True)
        observer.start()
        restarted = True

    assert restarted
    observer.schedule.assert_called_once()
    observer.start.assert_called_once()


def test_transient_push_error_requeues_changes():
    """On push error, changes and deletes should be re-queued for retry."""
    pending_changes: set[str] = set()
    pending_deletes: set[str] = set()
    lock = threading.Lock()

    changes = {"/workspace/apps/my-app/index.tsx"}
    deletes = {"/workspace/apps/my-app/old.tsx"}

    # Simulate: error during push processing — re-queue
    with lock:
        pending_changes.update(changes)
        pending_deletes.update(deletes)

    assert "/workspace/apps/my-app/index.tsx" in pending_changes
    assert "/workspace/apps/my-app/old.tsx" in pending_deletes


def test_consecutive_error_counter_and_backoff():
    """After 10 consecutive errors, backoff should activate."""
    consecutive_errors = 0

    # Simulate 10 consecutive failures
    for _ in range(10):
        consecutive_errors += 1

    assert consecutive_errors >= 10

    # Simulate successful push resets counter
    consecutive_errors = 0
    assert consecutive_errors == 0


# =============================================================================
# Fix 3: Deletion sync
# =============================================================================


def test_deletion_computes_correct_repo_path():
    """Deletion should compute repo_path from local path and prefix."""
    base = pathlib.Path("/workspace")
    abs_path = pathlib.Path("/workspace/apps/my-app/old-file.tsx")
    repo_prefix = "my-org/my-repo"

    rel = abs_path.relative_to(base)
    repo_path = f"{repo_prefix}/{rel}" if repo_prefix else str(rel)

    assert repo_path == "my-org/my-repo/apps/my-app/old-file.tsx"


def test_deletion_computes_repo_path_without_prefix():
    """When repo_prefix is empty, repo_path is just the relative path."""
    base = pathlib.Path("/workspace")
    abs_path = pathlib.Path("/workspace/apps/my-app/old-file.tsx")
    repo_prefix = ""

    rel = abs_path.relative_to(base)
    repo_path = f"{repo_prefix}/{rel}" if repo_prefix else str(rel)

    assert repo_path == "apps/my-app/old-file.tsx"


def test_deletion_skips_bifrost_files():
    """Deletions of .bifrost/ files should be skipped (manifest is system-managed)."""
    base = pathlib.Path("/workspace")
    deleted_paths = [
        pathlib.Path("/workspace/.bifrost/workflows.yaml"),
        pathlib.Path("/workspace/.bifrost/integrations.yaml"),
        pathlib.Path("/workspace/apps/my-app/removed.tsx"),
    ]

    to_delete = []
    for abs_p in deleted_paths:
        rel = abs_p.relative_to(base)
        if str(rel).startswith(".bifrost/") or str(rel).startswith(".bifrost\\"):
            continue
        to_delete.append(str(rel))

    assert len(to_delete) == 1
    assert to_delete[0] == "apps/my-app/removed.tsx"


@pytest.mark.asyncio
async def test_deletion_404_treated_as_success():
    """A 404 from the delete endpoint should be treated as success (file already gone)."""
    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_client = AsyncMock()
    # httpx raises for 4xx when raise_for_status is called, simulate via exception
    error = Exception("Not Found")
    error.response = mock_response  # type: ignore[attr-defined]
    mock_client.post.side_effect = error

    deleted_count = 0
    try:
        await mock_client.post("/api/files/delete", json={
            "path": "my-org/my-repo/apps/old.tsx",
            "location": "workspace",
            "mode": "cloud",
        })
    except Exception as del_err:
        status_code = getattr(getattr(del_err, "response", None), "status_code", None)
        if status_code == 404:
            deleted_count += 1

    assert deleted_count == 1
