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
from local_changes_viewer.gui.workers.diff_worker import DiffWorker

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


def test_scan_refresh_tick_does_not_empty_tree_while_repos_reappear(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the mid-scan empty-tree bug: on startup the tree
    paints from the on-disk cache, then the background rescan re-discovers
    repos already present in that cache via _on_repo_ready. Before the fix,
    _on_repo_ready blind-appended instead of merging by path, so the
    workspace ended up with two Repository entries per path; RepoTreeModel
    ._partition then treated each duplicate as the other's parent (a path is
    trivially relative_to itself), which knocked every top-level repo out of
    `roots` -- with all of them duplicated, the tree rendered as completely
    empty. On top of that the 150ms scan-refresh timer called
    _refresh_display() with preserve_tree=False (its default), i.e. a full
    clear()+rebuild on every tick, which is what actually painted that empty
    state to screen ~100x/scan. Both must be fixed: _on_repo_ready merges by
    path (no duplicate entries reach _partition), and the timer preserves
    the tree in place (no clear() at all, so even a transient anomaly
    wouldn't flash blank)."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    monkeypatch.setattr(window, "_refresh_watch_paths", lambda repo_paths: None)

    cached_workspace = Workspace(
        root_path=tmp_path,
        repositories=[
            _repo("repo_a", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]),
            _repo("repo_b", [FileChange(path=Path("b.py"), change_type=ChangeType.MODIFIED)]),
        ],
    )
    window._workspace = cached_workspace
    window._incremental_scan = False
    window._refresh_display()  # initial paint from cache (mirrors _start_scan L1288/1297)

    root_item = window._tree_view._model.invisibleRootItem()
    assert root_item.rowCount() == 2
    existing_repo_a_item = root_item.child(0)
    existing_repo_b_item = root_item.child(1)

    # The rescan re-discovers repos already present in the cached workspace.
    # Iterate a snapshot: window._workspace IS cached_workspace, and
    # _on_repo_ready mutates that same list, so iterating the live list
    # directly here would observe its own in-loop mutations.
    for repo in list(cached_workspace.repositories):
        window._on_repo_ready(repo)

    # This is exactly what the 150ms scan-refresh timer now invokes on every
    # tick (previously it invoked _refresh_display() directly, defaulting to
    # preserve_tree=False).
    window._on_scan_refresh_tick()

    root_item = window._tree_view._model.invisibleRootItem()
    try:
        assert root_item.rowCount() == 2, (
            f"expected 2 repo rows to survive the tick, tree has {root_item.rowCount()}"
        )
        # Same QStandardItem instances survive: proof the in-place
        # update_workspace()/_sync_level() diff ran, not a clear()+rebuild.
        assert root_item.child(0) is existing_repo_a_item
        assert root_item.child(1) is existing_repo_b_item
    finally:
        window.close()


def test_on_repo_ready_merges_by_path_instead_of_duplicating(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_on_repo_ready must replace the existing Repository entry for a path
    that's already in self._workspace.repositories (e.g. one carried over
    from the cached workspace a non-incremental scan starts from), never
    append a second entry for the same path -- see the empty-tree bug
    explained in test_scan_refresh_tick_does_not_empty_tree_while_repos_reappear."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()

    original_repo_a = _repo(
        "repo_a", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]
    )
    repo_b = _repo("repo_b", [FileChange(path=Path("b.py"), change_type=ChangeType.MODIFIED)])
    window._workspace = Workspace(root_path=tmp_path, repositories=[original_repo_a, repo_b])
    window._incremental_scan = False

    rescanned_repo_a = _repo(
        "repo_a",
        [
            FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("new.py"), change_type=ChangeType.ADDED),
        ],
    )

    try:
        window._on_repo_ready(rescanned_repo_a)

        assert len(window._workspace.repositories) == 2
        by_path = {r.path: r for r in window._workspace.repositories}
        assert by_path[original_repo_a.path] is rescanned_repo_a
        assert by_path[repo_b.path] is repo_b

        # A genuinely new repo path still appends rather than being dropped.
        new_repo = _repo("repo_c", [])
        window._on_repo_ready(new_repo)
        assert len(window._workspace.repositories) == 3
        assert window._workspace.repositories[-1] is new_repo
    finally:
        window.close()


def test_on_dead_repo_removes_repo_whose_directory_no_longer_exists(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_on_dead_repo is the removal counterpart to _on_repo_ready's
    merge-by-path (e3aac9b): once WorkspaceScannerService has positively
    confirmed a repo's directory is gone (git.exc.NoSuchPathError, surfaced
    as the "dead" scan trigger), the stale entry it carried over from a
    cached workspace must actually leave the merged tree instead of
    surviving every subsequent scan forever."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()

    repo_a = _repo("repo_a", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)])
    repo_gone = _repo(
        "repo_gone", [FileChange(path=Path("g.py"), change_type=ChangeType.MODIFIED)]
    )
    window._workspace = Workspace(root_path=tmp_path, repositories=[repo_a, repo_gone])
    window._incremental_scan = False

    try:
        window._on_dead_repo(repo_gone.path)

        assert window._workspace.repositories == [repo_a]
    finally:
        window.close()


def test_repo_absent_from_partial_scan_results_is_retained(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for e3aac9b: a scan that only reports a subset of
    repos (dirty-gated repos, a partial/in-progress scan, cache hits) must
    NOT cause the untouched repos to disappear from the merged workspace.
    Only an explicit _on_dead_repo call (a positive "this repo is gone"
    signal from the scanner) may remove a repo -- mere absence from this
    scan's on_repo_ready calls must never be treated the same way, or the
    tree would flicker empty exactly like the bug e3aac9b fixed."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()

    repo_a = _repo("repo_a", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)])
    repo_b = _repo("repo_b", [FileChange(path=Path("b.py"), change_type=ChangeType.MODIFIED)])
    window._workspace = Workspace(root_path=tmp_path, repositories=[repo_a, repo_b])
    window._incremental_scan = False

    # This scan only reports repo_b (e.g. repo_a wasn't dirty and was served
    # from cache, or the scan is still in progress); repo_a is never passed
    # to _on_repo_ready or _on_dead_repo at all this round.
    rescanned_repo_b = _repo(
        "repo_b",
        [
            FileChange(path=Path("b.py"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("new.py"), change_type=ChangeType.ADDED),
        ],
    )

    try:
        window._on_repo_ready(rescanned_repo_b)

        by_path = {r.path: r for r in window._workspace.repositories}
        assert len(window._workspace.repositories) == 2
        assert by_path[repo_a.path] is repo_a
        assert by_path[repo_b.path] is rescanned_repo_b
    finally:
        window.close()


def test_diff_worker_reports_plain_english_message_with_clicked_file_for_missing_repo(
    qapp, tmp_path: Path
) -> None:
    """GitRepoAdapter's underlying git.Repo(repo_path) raises
    git.exc.NoSuchPathError when the repo's directory is gone from disk (a
    worktree the user deleted outside the app); NoSuchPathError.__str__
    returns only the bare path, which used to surface verbatim as e.g.
    "Diff failed: /Users/.../EH-9952-retain-consent" with no explanation of
    what happened or which file the user clicked. DiffWorker must turn that
    into a plain-English message that names both."""
    repo_path = tmp_path / "deleted-worktree"  # never created -- genuinely does not exist
    change = FileChange(path=Path("some/file.py"), change_type=ChangeType.MODIFIED)
    worker = DiffWorker(repo_path, change)

    errors: list[str] = []
    worker.signals.error.connect(errors.append)
    worker.run()

    assert len(errors) == 1
    message = errors[0]
    assert "no longer exists" in message
    assert str(repo_path) in message
    assert str(change.path) in message


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
