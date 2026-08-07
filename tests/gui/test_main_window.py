import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import local_changes_viewer.gui.main_window as main_window_module
import local_changes_viewer.gui.settings as settings_module
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.gui.main_window import MainWindow

_BRANCH = BranchStatus(branch_name="main", ahead=0, behind=0)


def _repo(name: str, changes: list[FileChange]) -> Repository:
    return Repository(path=Path(f"/repos/{name}"), name=name, branch_status=_BRANCH, changes=changes)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirects AppSettings' QSettings to a throwaway ini file for the
    duration of the test, so constructing a MainWindow never reads or writes
    the developer's real ~/Library/Preferences state."""
    ini_path = tmp_path / "settings.ini"

    def _fake_qsettings(*_args, **_kwargs) -> QSettings:
        return QSettings(str(ini_path), QSettings.Format.IniFormat)

    monkeypatch.setattr(settings_module, "QSettings", _fake_qsettings)
    return ini_path


def _seed_settings(ini_path: Path, **values) -> None:
    settings = QSettings(str(ini_path), QSettings.Format.IniFormat)
    for key, value in values.items():
        settings.setValue(key, value)
    settings.sync()


def test_only_one_scan_starts_during_window_init(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the startup double-scan bug: _restore_last_folder()
    kicks off a scan, then _restore_window_state()'s setChecked() calls used
    to fire toggled handlers (e.g. include-unpushed-commits) that started a
    second, concurrent scan of the same workspace."""
    _seed_settings(
        isolated_settings,
        last_root_folder=str(tmp_path),
        include_unpushed_commits=True,
    )
    scan_calls: list[tuple] = []
    monkeypatch.setattr(
        MainWindow, "_start_scan", lambda self, *args, **kwargs: scan_calls.append((args, kwargs))
    )

    window = MainWindow()
    try:
        # Confirms the toggled signal we're relying on to reproduce the bug
        # actually fired (the setting differs from the QAction's own default).
        assert window._include_unpushed_commits_action.isChecked() is True
        assert len(scan_calls) == 1
    finally:
        window.close()


def test_display_filter_toggle_does_not_refresh_during_settings_restore(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_settings(
        isolated_settings,
        last_root_folder=str(tmp_path),
        ignore_md_files=True,
    )
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    refresh_calls: list[tuple] = []
    monkeypatch.setattr(
        MainWindow,
        "_refresh_display",
        lambda self, *args, **kwargs: refresh_calls.append((args, kwargs)),
    )

    window = MainWindow()
    try:
        assert window._ignore_md_action.isChecked() is True
        assert refresh_calls == []
    finally:
        window.close()


def test_auto_refresh_skipped_when_previous_scan_finished_too_recently(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    monkeypatch.undo()  # restore the real _start_scan for the rest of the test

    scan_worker_calls: list[object] = []
    window._thread_pool.start = scan_worker_calls.append
    window._root_folder = str(tmp_path)
    window._scan_in_progress = False
    window._last_scan_finished_at = time.monotonic()  # "just finished"

    try:
        window._start_scan(str(tmp_path), auto_refresh=True)
        assert scan_worker_calls == []
    finally:
        window.close()


def test_auto_refresh_proceeds_once_minimum_interval_has_elapsed(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    monkeypatch.undo()  # restore the real _start_scan for the rest of the test

    scan_worker_calls: list[object] = []
    window._thread_pool.start = scan_worker_calls.append
    window._root_folder = str(tmp_path)
    window._scan_in_progress = False
    window._last_scan_finished_at = time.monotonic() - 10.0  # well past the 5s minimum

    try:
        window._start_scan(str(tmp_path), auto_refresh=True)
        assert len(scan_worker_calls) == 1
    finally:
        window.close()


def test_on_workspace_ready_preserves_tree_in_place_when_tree_already_has_rows(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: on startup the tree is painted from the on-disk
    cache, then a full (non-incremental) background scan finishes. That
    must update the already-visible tree in place instead of clearing and
    rebuilding it -- clearing loses expansion/scroll state (the bug)."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    monkeypatch.setattr(window, "_refresh_watch_paths", lambda repo_paths: None)

    initial_workspace = Workspace(
        root_path=tmp_path,
        repositories=[
            _repo("repo_a", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)])
        ],
    )
    window._tree_view.set_workspace(initial_workspace)
    root_item = window._tree_view._model.invisibleRootItem()
    assert root_item.rowCount() == 1
    existing_repo_item = root_item.child(0)

    original_set_workspace = window._tree_view.set_workspace
    original_update_workspace = window._tree_view.update_workspace
    set_workspace_calls: list = []
    update_workspace_calls: list = []

    def _spy_set_workspace(workspace):
        set_workspace_calls.append(workspace)
        return original_set_workspace(workspace)

    def _spy_update_workspace(workspace):
        update_workspace_calls.append(workspace)
        return original_update_workspace(workspace)

    monkeypatch.setattr(window._tree_view, "set_workspace", _spy_set_workspace)
    monkeypatch.setattr(window._tree_view, "update_workspace", _spy_update_workspace)

    window._incremental_scan = False
    updated_workspace = Workspace(
        root_path=tmp_path,
        repositories=[
            _repo("repo_a", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)])
        ],
    )

    try:
        window._on_workspace_ready(updated_workspace)

        assert update_workspace_calls == [
            main_window_module.filter_workspace(
                updated_workspace,
                ignore_md_files=window._ignore_md_action.isChecked(),
                hide_repos_without_changes=window._hide_empty_repos_action.isChecked(),
                folder_filter_rules=window._folder_filter_rules,
                max_age_minutes=window._time_filter_minutes,
                profile=window._active_profile(),
            )
        ]
        assert set_workspace_calls == []
        # Same QStandardItem instance survives: proof the in-place
        # update_workspace()/_sync_level() diff ran, not set_workspace()'s
        # clear()-and-rebuild.
        assert window._tree_view._model.invisibleRootItem().child(0) is existing_repo_item
    finally:
        window.close()


def test_on_workspace_ready_rebuilds_when_tree_is_empty(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even with a non-incremental scan, an empty tree (nothing painted yet)
    still takes the full set_workspace() rebuild path."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    monkeypatch.setattr(window, "_refresh_watch_paths", lambda repo_paths: None)

    assert window._tree_view._model.invisibleRootItem().rowCount() == 0
    assert window._incremental_scan is False

    original_set_workspace = window._tree_view.set_workspace
    original_update_workspace = window._tree_view.update_workspace
    set_workspace_calls: list = []
    update_workspace_calls: list = []

    def _spy_set_workspace(workspace):
        set_workspace_calls.append(workspace)
        return original_set_workspace(workspace)

    def _spy_update_workspace(workspace):
        update_workspace_calls.append(workspace)
        return original_update_workspace(workspace)

    monkeypatch.setattr(window._tree_view, "set_workspace", _spy_set_workspace)
    monkeypatch.setattr(window._tree_view, "update_workspace", _spy_update_workspace)

    new_workspace = Workspace(
        root_path=tmp_path,
        repositories=[
            _repo("repo_a", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)])
        ],
    )

    try:
        window._on_workspace_ready(new_workspace)

        assert len(set_workspace_calls) == 1
        assert update_workspace_calls == []
        assert window._tree_view._model.invisibleRootItem().rowCount() == 1
    finally:
        window.close()
