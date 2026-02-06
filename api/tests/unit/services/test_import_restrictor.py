import sys
import pytest
from unittest.mock import patch

from src.services.execution.import_restrictor import (
    WorkspaceImportRestrictor,
    install_import_restrictions,
    remove_import_restrictions,
    get_active_restrictors,
)


@pytest.fixture(autouse=True)
def cleanup_meta_path():
    yield
    remove_import_restrictions()


class TestWorkspaceImportRestrictorInit:

    def test_valid_absolute_paths(self, tmp_path):
        r = WorkspaceImportRestrictor([str(tmp_path)])
        assert len(r.workspace_paths) == 1

    def test_valid_absolute_paths_with_home(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        r = WorkspaceImportRestrictor([str(tmp_path)], home_path=str(home))
        assert r.home_path is not None

    def test_relative_path_raises_valueerror(self):
        with pytest.raises(ValueError, match="must be absolute"):
            WorkspaceImportRestrictor(["relative/path"])

    def test_mixed_paths_relative_raises(self, tmp_path):
        with pytest.raises(ValueError, match="must be absolute"):
            WorkspaceImportRestrictor([str(tmp_path), "not/absolute"])

    def test_no_home_path(self, tmp_path):
        r = WorkspaceImportRestrictor([str(tmp_path)])
        assert r.home_path is None


class TestIsBlockedImport:

    @pytest.fixture
    def restrictor(self, tmp_path):
        return WorkspaceImportRestrictor([str(tmp_path)])

    def test_src_prefix_blocked(self, restrictor):
        assert restrictor._is_blocked_import("src.models") is True

    def test_src_dot_anything_blocked(self, restrictor):
        assert restrictor._is_blocked_import("src.services.something") is True

    def test_bifrost_not_blocked(self, restrictor):
        assert restrictor._is_blocked_import("bifrost.workflows") is False

    def test_os_not_blocked(self, restrictor):
        assert restrictor._is_blocked_import("os") is False

    def test_stdlib_json_not_blocked(self, restrictor):
        assert restrictor._is_blocked_import("json") is False

    def test_exact_src_dot_blocked(self, restrictor):
        assert restrictor._is_blocked_import("src.") is True

    def test_src_without_dot_not_blocked(self, restrictor):
        assert restrictor._is_blocked_import("src") is False


class TestIsWorkspaceCode:

    @pytest.fixture
    def restrictor(self, tmp_path):
        return WorkspaceImportRestrictor([str(tmp_path)])

    def test_file_inside_workspace(self, tmp_path, restrictor):
        filepath = str(tmp_path / "subdir" / "module.py")
        assert restrictor._is_workspace_code(filepath) is True

    def test_file_at_workspace_root(self, tmp_path, restrictor):
        filepath = str(tmp_path / "module.py")
        assert restrictor._is_workspace_code(filepath) is True

    def test_file_outside_workspace(self, restrictor):
        assert restrictor._is_workspace_code("/some/other/path/module.py") is False

    def test_multiple_workspace_paths(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        r = WorkspaceImportRestrictor([str(dir_a), str(dir_b)])
        assert r._is_workspace_code(str(dir_b / "test.py")) is True
        assert r._is_workspace_code(str(dir_a / "test.py")) is True


class TestIsHomeCode:

    def test_file_inside_home(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        r = WorkspaceImportRestrictor([str(tmp_path)], home_path=str(home))
        assert r._is_home_code(str(home / "workflow.py")) is True

    def test_file_outside_home(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        r = WorkspaceImportRestrictor([str(tmp_path)], home_path=str(home))
        assert r._is_home_code(str(tmp_path / "platform" / "wf.py")) is False

    def test_no_home_path_returns_false(self, tmp_path):
        r = WorkspaceImportRestrictor([str(tmp_path)])
        assert r._is_home_code(str(tmp_path / "anything.py")) is False


class TestInstallRemoveGetActive:

    def test_install_adds_restrictor(self, tmp_path):
        install_import_restrictions([str(tmp_path)])
        actives = get_active_restrictors()
        assert len(actives) == 1
        assert isinstance(actives[0], WorkspaceImportRestrictor)

    def test_install_adds_to_front_of_meta_path(self, tmp_path):
        install_import_restrictions([str(tmp_path)])
        assert isinstance(sys.meta_path[0], WorkspaceImportRestrictor)

    def test_remove_clears_all_restrictors(self, tmp_path):
        install_import_restrictions([str(tmp_path)])
        install_import_restrictions([str(tmp_path)])
        assert len(get_active_restrictors()) == 2
        remove_import_restrictions()
        assert len(get_active_restrictors()) == 0

    def test_get_active_empty_by_default(self):
        assert get_active_restrictors() == []

    def test_install_empty_paths_raises(self):
        with pytest.raises(ValueError, match="At least one workspace path"):
            install_import_restrictions([])

    def test_install_with_home_path(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        install_import_restrictions([str(tmp_path)], home_path=str(home))
        actives = get_active_restrictors()
        assert actives[0].home_path is not None


class TestFindSpec:

    @pytest.fixture
    def restrictor(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        return WorkspaceImportRestrictor([str(tmp_path)], home_path=str(home))

    def test_non_blocked_module_returns_none(self, restrictor):
        assert restrictor.find_spec("os") is None

    def test_allowed_export_returns_none(self, restrictor):
        assert restrictor.find_spec("bifrost.workflows") is None

    def test_allowed_export_src_sdk(self, restrictor):
        assert restrictor.find_spec("src.sdk.decorators") is None

    def test_allowed_export_src_models(self, restrictor):
        assert restrictor.find_spec("src.models.models") is None

    def test_blocked_import_no_caller_returns_none(self, restrictor):
        with patch.object(restrictor, "_get_caller_info", return_value=None):
            assert restrictor.find_spec("src.services.something") is None

    def test_blocked_import_from_home_raises(self, restrictor, tmp_path):
        home_file = str(tmp_path / "home" / "workflow.py")
        with patch.object(restrictor, "_get_caller_info", return_value=(home_file, True)):
            with pytest.raises(ImportError, match="cannot import 'src.services.internal'"):
                restrictor.find_spec("src.services.internal")

    def test_blocked_import_from_platform_returns_none(self, restrictor, tmp_path):
        platform_file = str(tmp_path / "platform" / "workflow.py")
        with patch.object(restrictor, "_get_caller_info", return_value=(platform_file, False)):
            assert restrictor.find_spec("src.services.internal") is None

    def test_home_import_error_mentions_bifrost_sdk(self, restrictor, tmp_path):
        home_file = str(tmp_path / "home" / "workflow.py")
        with patch.object(restrictor, "_get_caller_info", return_value=(home_file, True)):
            with pytest.raises(ImportError, match="Bifrost SDK"):
                restrictor.find_spec("src.handlers.admin")

    def test_all_allowed_exports_pass(self, restrictor):
        for export in WorkspaceImportRestrictor.ALLOWED_EXPORTS:
            assert restrictor.find_spec(export) is None

    def test_bifrost_base_allowed(self, restrictor):
        assert restrictor.find_spec("bifrost") is None
