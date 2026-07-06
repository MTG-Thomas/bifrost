import pytest

from scripts.check_mtg_ci_boundaries import _repo_root, _resolve_workflow_path


def test_resolve_workflow_path_rejects_paths_outside_repo():
    outside = _repo_root().parent / "outside.yml"
    with pytest.raises(ValueError, match="must stay within"):
        _resolve_workflow_path(outside)


def test_repo_root_never_resolves_to_filesystem_root():
    root = _repo_root()
    assert root != root.parent
