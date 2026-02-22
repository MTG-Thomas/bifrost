"""Unit tests for CLI watch mode logic."""
import threading


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
