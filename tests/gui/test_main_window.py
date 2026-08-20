import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import git
import pytest
from PySide6.QtCore import QModelIndex, QSettings, Qt
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMessageBox

import local_changes_viewer.gui.main_window as main_window_module
import local_changes_viewer.gui.main_window_context_menu as main_window_context_menu_module
import local_changes_viewer.gui.settings as settings_module
from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.gui import applog
from local_changes_viewer.gui.diff_view import diff_view_widget as diff_view_widget_module
from local_changes_viewer.gui.error_log_dialog import ErrorLogDialog
from local_changes_viewer.gui.main_window import MainWindow
from local_changes_viewer.gui.workers.diff_worker import DiffWorker
from local_changes_viewer.gui.workspace_tree.tree_model import FOLDER_PATH_ROLE, NODE_KEY_ROLE

_BRANCH = BranchStatus(branch_name="main", ahead=0, behind=0)


def _stub_diff() -> DiffResult:
    return DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[])


def _patch_question_reply(monkeypatch: pytest.MonkeyPatch, reply) -> None:
    """Bug 2's confirmation modal (confirm_and_clear_diff) lives in
    diff_view_widget.py, so its QMessageBox is patched there. Bug 4's
    confirmation lives in main_window.py's own _on_file_selected, which
    imports QMessageBox directly -- patched on main_window_module too, or a
    real (invisible-but-still-modal-event-loop) QMessageBox.question() would
    block the test forever waiting for a button click that never comes,
    even under the offscreen platform."""
    stub = SimpleNamespace(question=lambda *a, **k: reply, StandardButton=QMessageBox.StandardButton)
    monkeypatch.setattr(diff_view_widget_module, "QMessageBox", stub)
    monkeypatch.setattr(main_window_module, "QMessageBox", stub)


def _type_marker_into_edit_buffer(diff_view) -> None:
    right = diff_view._side_by_side._right
    cursor = right.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    right.setTextCursor(cursor)
    QTest.keyClicks(right, "MARKER")


def _repo(name: str, changes: list[FileChange]) -> Repository:
    return Repository(path=Path(f"/repos/{name}"), name=name, branch_status=_BRANCH, changes=changes)


def _repo_row_present(model, path: Path, parent: QModelIndex = QModelIndex()) -> bool:
    """Walks every row looking for a repo/nested-repo item keyed to `path`
    (NODE_KEY_ROLE), mirroring _find_folder_index below but for repo rows."""
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        if index.data(NODE_KEY_ROLE) == str(path):
            return True
        if _repo_row_present(model, path, index):
            return True
    return False


def _find_folder_index(
    model, folder_path: Path, parent: QModelIndex = QModelIndex()
) -> QModelIndex:
    """Mirrors MainWindow._find_tree_index's walk, but for a folder row
    (matched on FOLDER_PATH_ROLE rather than FILE_CHANGE_ROLE/REPO_PATH_ROLE),
    since there's no production helper for that -- only file rows need one
    outside tests (see MainWindow._restore_previous_selection)."""
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        if index.data(FOLDER_PATH_ROLE) == str(folder_path):
            return index
        found = _find_folder_index(model, folder_path, index)
        if found.isValid():
            return found
    return QModelIndex()


def _init_real_repo(repo_path: Path) -> git.Repo:
    """Builds a real git repo on disk -- Create Patch's whole point is a
    patch real git commands accept, so its reachability tests need a real
    repo behind the tree, not the fake `/repos/name` paths `_repo()` uses for
    tests that never touch git itself."""
    repo_path.mkdir()
    repo = git.Repo.init(repo_path, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test User")
        cw.set_value("user", "email", "test@example.com")
    return repo


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


@pytest.fixture(autouse=True)
def _reset_applog_errors():
    """applog's ERROR store (_ERROR_ENTRIES) is a module-level global shared
    by the whole test process -- without this, an error logged by one test
    in this file (e.g. via _report_error) would still be sitting in
    applog.recent_errors() when the next test builds a "fresh" window and
    asserts the indicator starts hidden."""
    applog.clear_errors()
    yield
    applog.clear_errors()


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


def test_stale_scan_result_dropped_when_superseded_by_newer_scan(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: opening folder A (slow scan), then switching to
    folder B before A's ScanWorker reports back, must not let A's stale
    workspace_ready result replace B's tree or overwrite the on-disk cache
    -- _scan_generation (bumped on every _start_scan call) must make the
    handlers drop a result from a scan a newer one already superseded."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    monkeypatch.undo()  # restore the real _start_scan for the rest of the test

    started_workers: list[object] = []
    window._thread_pool.start = started_workers.append
    save_calls: list[Workspace] = []
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: save_calls.append(workspace))
    monkeypatch.setattr(window, "_refresh_watch_paths", lambda repo_paths: None)

    folder_a = tmp_path / "a"
    folder_a.mkdir()
    folder_b = tmp_path / "b"
    folder_b.mkdir()

    try:
        window._start_scan(str(folder_a))
        assert len(started_workers) == 1
        worker_a = started_workers[0]

        # Switch root folders before A's worker reports back -- this is
        # what _set_root_folder does on "Open Folder".
        window._start_scan(str(folder_b))
        assert len(started_workers) == 2
        worker_b = started_workers[1]

        workspace_a = Workspace(root_path=folder_a, repositories=[_repo("stale", [])])
        workspace_b = Workspace(root_path=folder_b, repositories=[_repo("fresh", [])])

        # A's superseded result arrives first -- it must be dropped, not
        # clobber B's in-progress scan or hit the on-disk cache.
        worker_a.signals.workspace_ready.emit(workspace_a)
        assert window._workspace is not workspace_a
        assert window._scan_in_progress is True
        assert save_calls == []

        # B's result, from the current generation, must apply normally.
        worker_b.signals.workspace_ready.emit(workspace_b)
        assert window._workspace is workspace_b
        assert window._scan_in_progress is False
        assert save_calls == [workspace_b]
    finally:
        window.close()


def test_user_initiated_refresh_supersedes_in_progress_scan(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_on_refresh (and the include-ignored/include-unpushed-commits toggle
    handlers) deliberately have no _scan_in_progress guard -- unlike the
    auto-refresh/file-watcher handlers, silently swallowing a user's click
    would be bad UX. Clicking Refresh again while a scan is still running
    must start a second scan that supersedes the first (via
    _scan_generation) rather than leaving _scan_in_progress in an
    inconsistent state or letting the first scan's stale result win."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    monkeypatch.undo()

    started_workers: list[object] = []
    window._thread_pool.start = started_workers.append
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    monkeypatch.setattr(window, "_refresh_watch_paths", lambda repo_paths: None)
    window._root_folder = str(tmp_path)

    try:
        window._on_refresh()
        assert len(started_workers) == 1
        assert window._scan_in_progress is True
        first_worker = started_workers[0]

        # User clicks Refresh again before the first scan finishes.
        window._on_refresh()
        assert len(started_workers) == 2
        assert window._scan_in_progress is True
        second_worker = started_workers[1]

        stale_workspace = Workspace(root_path=Path(tmp_path), repositories=[_repo("stale", [])])
        first_worker.signals.workspace_ready.emit(stale_workspace)
        # Dropped: must not flip _scan_in_progress off while the second
        # (current) scan is still running.
        assert window._scan_in_progress is True
        assert window._workspace is not stale_workspace

        fresh_workspace = Workspace(root_path=Path(tmp_path), repositories=[_repo("fresh", [])])
        second_worker.signals.workspace_ready.emit(fresh_workspace)
        assert window._scan_in_progress is False
        assert window._workspace is fresh_workspace
    finally:
        window.close()


def test_worker_signal_after_shutdown_requested_is_dropped(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the closeEvent teardown race: closeEvent sets
    _shutdown_requested before waiting (with a bounded timeout) for the
    thread pool, so a worker that outlives that wait can still emit later.
    _guard_worker_result must drop that late signal instead of calling back
    into a window that may already be torn down."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    monkeypatch.undo()

    started_workers: list[object] = []
    window._thread_pool.start = started_workers.append
    save_calls: list[Workspace] = []
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: save_calls.append(workspace))
    monkeypatch.setattr(window, "_refresh_watch_paths", lambda repo_paths: None)

    try:
        window._start_scan(str(tmp_path))
        worker = started_workers[0]

        # Simulate closeEvent having already set the shutdown flag (it does
        # this before the bounded waitForDone) while this worker is still
        # mid-flight.
        window._shutdown_requested.set()

        workspace = Workspace(root_path=Path(tmp_path), repositories=[_repo("late", [])])
        worker.signals.workspace_ready.emit(workspace)

        # The guarded slot must have short-circuited before touching
        # _on_workspace_ready at all.
        assert window._workspace is not workspace
        assert save_calls == []
    finally:
        window._shutdown_requested.clear()
        window.close()


def test_update_file_info_label_caps_read_to_sniff_size(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: _update_file_info_label used to read_bytes() the
    whole file on the GUI thread on every file selection. A file whose first
    _FILE_INFO_SNIFF_BYTES are plain ASCII but which contains a NUL byte
    further in must still be reported as text (not "Binary"), proving the
    read never reaches that NUL -- i.e. it's bounded, not just fast."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    monkeypatch.undo()
    monkeypatch.setattr(main_window_module, "_FILE_INFO_SNIFF_BYTES", 16)

    repo_path = tmp_path
    file_path = repo_path / "big.bin"
    file_path.write_bytes(b"a" * 16 + b"\x00" + b"b" * 10_000)
    change = FileChange(path=Path("big.bin"), change_type=ChangeType.MODIFIED)

    try:
        window._update_file_info_label(repo_path, change)
        assert window._file_info_label.text() == "UTF-8 · N/A"
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


# ---------------------------------------------------------------------------
# Bug 2: Ignore Whitespace (and, by the same _load_diff choke point, Refresh
# Diff) must not silently wipe unsaved edits -- clear_diff() never consulted
# has_unsaved_edits() at all before this fix.
# ---------------------------------------------------------------------------


def test_ignore_whitespace_toggle_with_unsaved_edits_prompts_and_declining_preserves_buffer(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        window._thread_pool.start = lambda *a, **k: None  # no real DiffWorker must run
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        file_path = repo_path / "sample.txt"
        file_path.write_text("one\ntwo\nthree")
        change = FileChange(
            path=Path("sample.txt"), change_type=ChangeType.MODIFIED, diff=_stub_diff()
        )
        window._selected_change = change
        window._selected_repo_path = repo_path
        window._diff_view.set_diff(change.diff, str(change.path), file_path)
        window._diff_view.show()
        QTest.qWaitForWindowExposed(window._diff_view)

        window._diff_view._edit_button.setChecked(True)
        _type_marker_into_edit_buffer(window._diff_view)
        assert window._diff_view.has_unsaved_edits()

        _patch_question_reply(monkeypatch, QMessageBox.StandardButton.No)

        # The real checkable QAction, not a direct call to
        # _on_ignore_whitespace_toggled -- this is exactly what the
        # Settings-menu checkbox click drives.
        window._ignore_whitespace_action.setChecked(
            not window._ignore_whitespace_action.isChecked()
        )

        assert window._diff_view.has_unsaved_edits(), "declining must preserve the buffer"
        assert "MARKER" in window._diff_view._side_by_side._right.toPlainText()
    finally:
        window._diff_view.discard_edits_if_any()
        window.close()


# ---------------------------------------------------------------------------
# Bug 4: clicking a different file in the tree while mid-edit must ask, and
# declining must leave both the diff buffer AND the tree's own selection
# highlight on the originally-edited file (Qt has already moved the
# highlight onto the newly-clicked row by the time this handler runs).
# ---------------------------------------------------------------------------


def test_switching_files_mid_edit_prompts_and_declining_restores_tree_selection(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        window._always_reload_diff_action.setChecked(False)
        repo_path = tmp_path / "repo"
        repo_path.mkdir()
        (repo_path / "file_a.py").write_text("a\n")
        (repo_path / "file_b.py").write_text("b\n")
        change_a = FileChange(
            path=Path("file_a.py"), change_type=ChangeType.MODIFIED, diff=_stub_diff()
        )
        change_b = FileChange(
            path=Path("file_b.py"), change_type=ChangeType.MODIFIED, diff=_stub_diff()
        )
        workspace = Workspace(
            root_path=tmp_path,
            repositories=[
                Repository(
                    path=repo_path, name="repo", branch_status=_BRANCH,
                    changes=[change_a, change_b],
                )
            ],
        )
        window._tree_view.set_workspace(workspace)
        model = window._tree_view.model()
        index_a = MainWindow._find_tree_index(model, repo_path, change_a)
        index_b = MainWindow._find_tree_index(model, repo_path, change_b)
        assert index_a.isValid() and index_b.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)

        window._tree_view.setCurrentIndex(index_a)
        assert window._selected_change is change_a

        window._diff_view._edit_button.setChecked(True)
        _type_marker_into_edit_buffer(window._diff_view)
        assert window._diff_view.has_unsaved_edits()

        _patch_question_reply(monkeypatch, QMessageBox.StandardButton.No)

        # The real tree row change, exactly what a click on file_b's row
        # drives -- Qt moves the tree's current index to index_b as part of
        # this call, before _on_file_selected(change_b) ever runs.
        window._tree_view.setCurrentIndex(index_b)

        assert window._selected_change is change_a, "selection must stay on file_a"
        assert window._diff_view.has_unsaved_edits(), "buffer must survive the decline"
        assert "MARKER" in window._diff_view._side_by_side._right.toPlainText()
        assert window._tree_view.currentIndex() == index_a, (
            "tree highlight must move back onto file_a, not stay on the "
            "file_b row Qt had already highlighted"
        )
    finally:
        window._diff_view.discard_edits_if_any()
        window.close()


# ---------------------------------------------------------------------------
# "Create patch" context-menu action. This repo has shipped dead menu items
# before (see other regression tests in this file), so reachability is
# proven by actually triggering the QAction found in a real captured QMenu --
# not by grepping _on_tree_context_menu's source for the string.
# ---------------------------------------------------------------------------


def _capture_menu(monkeypatch: pytest.MonkeyPatch) -> list:
    """Intercepts the QMenu(s) _on_tree_context_menu builds without ever
    popping one up -- a real exec() blocks in a native modal event loop
    forever under the offscreen platform, with no click ever coming to close
    it. Monkeypatching QMenu.exec directly (rather than subclassing) does NOT
    stop the real popup: PySide6 dispatches exec() through the C++ vtable, so
    only a genuine QMenu subclass overriding exec() is honored -- reassigning
    the base class's attribute in pure Python is silently ignored here.
    """
    captured: list = []

    class _NonBlockingMenu(QMenu):
        def exec(self, *args, **kwargs) -> None:
            captured.append(self)

    monkeypatch.setattr(main_window_context_menu_module, "QMenu", _NonBlockingMenu)
    return captured


def _trigger_create_patch(menu: QMenu) -> None:
    action = next(a for a in menu.actions() if a.text() == "Create patch")
    action.trigger()


def _install_fake_selection_dialog(
    monkeypatch: pytest.MonkeyPatch,
    accepted: bool = True,
    selected_paths: list[Path] | None = None,
) -> list:
    """Stands in for the real PatchFileSelectionDialog -- a real one would
    block in a modal event loop forever under the offscreen platform, same
    problem _install_fake_chooser below solves for the patch destination
    chooser. Unlike QMenu (dispatched through the C++ vtable, see
    _capture_menu above), main_window.py constructs this dialog through its
    own module-level name, so substituting the whole class is enough -- no
    subclass-exec trick needed here.

    `selected_paths` defaults to every path the dialog was constructed with
    (mirrors "all checked by default"); pass an explicit subset to simulate
    the user unchecking rows before accepting.
    """
    constructed: list = []

    class _FakeSelectionDialog:
        def __init__(self, changes, parent=None) -> None:
            self.changes = list(changes)
            constructed.append(self)

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

        def selected_paths(self) -> list[Path]:
            if selected_paths is not None:
                return list(selected_paths)
            return [change.path for change in self.changes]

    monkeypatch.setattr(main_window_module, "PatchFileSelectionDialog", _FakeSelectionDialog)
    return constructed


def test_create_patch_action_reachable_and_wired_for_a_file_row(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "tracked.txt").write_text("original\n")
        repo.index.add(["tracked.txt"])
        repo.index.commit("initial commit")
        (repo_path / "tracked.txt").write_text("changed\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        change = next(c for c in changes if c.path == Path("tracked.txt"))
        index = MainWindow._find_tree_index(window._tree_view.model(), repo_path, change)
        assert index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)
        captured_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(index).center())
        assert len(captured_menus) == 1

        _install_fake_selection_dialog(monkeypatch)
        captured_patches: list = []
        monkeypatch.setattr(
            window,
            "_present_patch",
            lambda patch, name: captured_patches.append((patch, name)),
        )
        _trigger_create_patch(captured_menus[0])

        assert len(captured_patches) == 1
        patch, suggested_name = captured_patches[0]
        assert "diff --git a/tracked.txt b/tracked.txt" in patch
        assert suggested_name == "tracked.txt.patch"
    finally:
        window.close()


def test_create_patch_action_reachable_and_wired_for_a_plain_folder_row(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "sub").mkdir()
        # A tracked file must already exist under "sub" before the untracked
        # one is added -- otherwise git status (and thus list_changes) never
        # descends into "sub" at all and reports it as one collapsed
        # untracked-directory entry (see GitRepoAdapter.list_changes), which
        # renders as a FILE_CHANGE_ROLE row, not a real FOLDER_PATH_ROLE node.
        (repo_path / "sub" / "tracked.txt").write_text("tracked\n")
        repo.index.add(["sub/tracked.txt"])
        repo.index.commit("add sub/tracked.txt")
        (repo_path / "sub" / "new_file.txt").write_text("brand new\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        folder_index = _find_folder_index(window._tree_view.model(), repo_path / "sub")
        assert folder_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)
        captured_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(folder_index).center())
        assert len(captured_menus) == 1

        _install_fake_selection_dialog(monkeypatch)
        captured_patches: list = []
        monkeypatch.setattr(
            window,
            "_present_patch",
            lambda patch, name: captured_patches.append((patch, name)),
        )
        _trigger_create_patch(captured_menus[0])

        assert len(captured_patches) == 1
        patch, suggested_name = captured_patches[0]
        assert "sub/new_file.txt" in patch
        assert suggested_name == "sub.patch"
    finally:
        window.close()


def test_create_patch_action_reachable_and_wired_for_a_repo_root_row(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "tracked.txt").write_text("original\n")
        repo.index.add(["tracked.txt"])
        repo.index.commit("initial commit")
        (repo_path / "tracked.txt").write_text("changed\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        repo_index = window._tree_view.find_repo_index(repo_path)
        assert repo_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)
        captured_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(repo_index).center())
        assert len(captured_menus) == 1

        _install_fake_selection_dialog(monkeypatch)
        captured_patches: list = []
        monkeypatch.setattr(
            window,
            "_present_patch",
            lambda patch, name: captured_patches.append((patch, name)),
        )
        _trigger_create_patch(captured_menus[0])

        assert len(captured_patches) == 1
        patch, suggested_name = captured_patches[0]
        assert "diff --git a/tracked.txt b/tracked.txt" in patch
        assert suggested_name == "repo.patch"
    finally:
        window.close()


def test_create_patch_deselecting_a_file_keeps_its_hunks_out_of_the_clipboard_patch(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the selection dialog: a file the user leaves unchecked
    must not merely be absent from `_present_patch`'s argument in isolation
    (already proven by the reachability tests above via a mocked
    _present_patch) -- its hunks must genuinely never reach the clipboard the
    real _present_patch writes to, end to end through a real git repo."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "keep.txt").write_text("original keep\n")
        (repo_path / "drop.txt").write_text("original drop\n")
        repo.index.add(["keep.txt", "drop.txt"])
        repo.index.commit("initial commit")
        (repo_path / "keep.txt").write_text("changed keep\n")
        (repo_path / "drop.txt").write_text("changed drop\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        repo_index = window._tree_view.find_repo_index(repo_path)
        assert repo_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)
        captured_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(repo_index).center())
        assert len(captured_menus) == 1

        _install_fake_selection_dialog(monkeypatch, selected_paths=[Path("keep.txt")])
        _install_fake_chooser(monkeypatch, click_target="copy")

        _trigger_create_patch(captured_menus[0])

        clipboard_text = QGuiApplication.clipboard().text()
        assert "diff --git a/keep.txt b/keep.txt" in clipboard_text
        assert "-original keep" in clipboard_text
        assert "drop.txt" not in clipboard_text
    finally:
        window.close()


def test_create_patch_cancel_in_selection_dialog_aborts_with_no_clipboard_write_and_no_file_picker(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "tracked.txt").write_text("original\n")
        repo.index.add(["tracked.txt"])
        repo.index.commit("initial commit")
        (repo_path / "tracked.txt").write_text("changed\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        change = next(c for c in changes if c.path == Path("tracked.txt"))
        index = MainWindow._find_tree_index(window._tree_view.model(), repo_path, change)
        assert index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)
        captured_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(index).center())
        assert len(captured_menus) == 1

        _install_fake_selection_dialog(monkeypatch, accepted=False)
        QGuiApplication.clipboard().setText("untouched")

        def _fail_if_constructed(*_args, **_kwargs):
            raise AssertionError("chooser must never open when the selection dialog is cancelled")

        monkeypatch.setattr(main_window_module.QMessageBox, "__init__", _fail_if_constructed)

        def _fail_if_file_picker_opened(*_args, **_kwargs):
            raise AssertionError("file picker must never open when the selection dialog is cancelled")

        monkeypatch.setattr(
            main_window_module,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=_fail_if_file_picker_opened),
        )

        _trigger_create_patch(captured_menus[0])

        assert QGuiApplication.clipboard().text() == "untouched"
    finally:
        window.close()


def _install_fake_chooser(monkeypatch: pytest.MonkeyPatch, click_target: str | None) -> dict:
    """Stands in for the real QMessageBox chooser _present_patch constructs --
    a real one would block in a modal event loop forever under the offscreen
    platform, since nothing ever clicks it. `click_target` names which button
    clickedButton() should report as clicked ("copy"/"save"/None for Cancel).
    Static-method calls (information/warning) are recorded so the empty-patch
    and save-failure paths can be asserted without needing any instance."""
    calls: dict = {"information": [], "warning": []}

    class _FakeChooser:
        ButtonRole = QMessageBox.ButtonRole
        StandardButton = QMessageBox.StandardButton

        def __init__(self, *_args, **_kwargs) -> None:
            self._by_kind: dict = {}

        def setWindowTitle(self, *_args, **_kwargs) -> None:
            pass

        def setText(self, *_args, **_kwargs) -> None:
            pass

        def addButton(self, *args):
            if args and args[0] == "Copy to Clipboard":
                kind = "copy"
            elif args and args[0] == "Save to Disk…":
                kind = "save"
            else:
                kind = "cancel"
            button = object()
            self._by_kind[kind] = button
            return button

        def setDefaultButton(self, *_args, **_kwargs) -> None:
            pass

        def setEscapeButton(self, *_args, **_kwargs) -> None:
            pass

        def exec(self) -> None:
            pass

        def clickedButton(self):
            return self._by_kind.get(click_target)

        @staticmethod
        def information(*args, **kwargs) -> None:
            calls["information"].append((args, kwargs))

        @staticmethod
        def warning(*args, **kwargs) -> None:
            calls["warning"].append((args, kwargs))

    monkeypatch.setattr(main_window_module, "QMessageBox", _FakeChooser)
    return calls


def test_present_patch_copy_to_clipboard_sets_clipboard_and_status(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        _install_fake_chooser(monkeypatch, click_target="copy")

        window._present_patch("diff --git a/x b/x\n", "x.patch")

        assert QGuiApplication.clipboard().text() == "diff --git a/x b/x\n"
        assert "clipboard" in window.statusBar().currentMessage().lower()
    finally:
        window.close()


def test_present_patch_save_to_disk_writes_file_at_chosen_path(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        _install_fake_chooser(monkeypatch, click_target="save")
        destination = tmp_path / "chosen.patch"
        monkeypatch.setattr(
            main_window_module,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *a, **k: (str(destination), "")),
        )

        window._present_patch("diff --git a/x b/x\n", "x.patch")

        assert destination.read_text(encoding="utf-8") == "diff --git a/x b/x\n"
        assert str(destination) in window.statusBar().currentMessage()
    finally:
        window.close()


def test_present_patch_save_dialog_cancelled_writes_nothing(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        _install_fake_chooser(monkeypatch, click_target="save")
        monkeypatch.setattr(
            main_window_module,
            "QFileDialog",
            SimpleNamespace(getSaveFileName=lambda *a, **k: ("", "")),
        )

        window._present_patch("diff --git a/x b/x\n", "x.patch")

        assert list(tmp_path.iterdir()) == []
    finally:
        window.close()


def test_present_patch_cancel_writes_nothing_and_leaves_clipboard_untouched(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        QGuiApplication.clipboard().setText("untouched")
        _install_fake_chooser(monkeypatch, click_target=None)

        window._present_patch("diff --git a/x b/x\n", "x.patch")

        assert QGuiApplication.clipboard().text() == "untouched"
    finally:
        window.close()


def test_present_patch_empty_patch_shows_info_and_never_opens_chooser(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        calls = _install_fake_chooser(monkeypatch, click_target="copy")

        def _fail_if_constructed(*_args, **_kwargs):
            raise AssertionError("chooser must never open for an empty patch")

        monkeypatch.setattr(main_window_module.QMessageBox, "__init__", _fail_if_constructed)

        window._present_patch("", "x.patch")

        assert len(calls["information"]) == 1
    finally:
        window.close()


# ---------------------------------------------------------------------------
# "Apply patch...": the repo-root-only inverse of "Create patch".
# ---------------------------------------------------------------------------


def _install_fake_source_chooser(monkeypatch: pytest.MonkeyPatch, click_target: str | None) -> dict:
    """Stands in for the QMessageBox `_on_apply_patch_for_repo` builds to ask
    "From File…" / "From Clipboard" / Cancel -- same shape as
    `_install_fake_chooser` above (which covers "Create patch"'s destination
    chooser instead), but with this dialog's own button labels and with
    `critical` recorded too, since a failed apply reports through it.
    """
    calls: dict = {"information": [], "warning": [], "critical": []}

    class _FakeSourceChooser:
        ButtonRole = QMessageBox.ButtonRole
        StandardButton = QMessageBox.StandardButton

        def __init__(self, *_args, **_kwargs) -> None:
            self._by_kind: dict = {}

        def setWindowTitle(self, *_args, **_kwargs) -> None:
            pass

        def setText(self, *_args, **_kwargs) -> None:
            pass

        def addButton(self, *args):
            if args and args[0] == "From File…":
                kind = "file"
            elif args and args[0] == "From Clipboard":
                kind = "clipboard"
            else:
                kind = "cancel"
            button = object()
            self._by_kind[kind] = button
            return button

        def setDefaultButton(self, *_args, **_kwargs) -> None:
            pass

        def setEscapeButton(self, *_args, **_kwargs) -> None:
            pass

        def exec(self) -> None:
            pass

        def clickedButton(self):
            return self._by_kind.get(click_target)

        @staticmethod
        def information(*args, **kwargs) -> None:
            calls["information"].append((args, kwargs))

        @staticmethod
        def warning(*args, **kwargs) -> None:
            calls["warning"].append((args, kwargs))

        @staticmethod
        def critical(*args, **kwargs) -> None:
            calls["critical"].append((args, kwargs))

    monkeypatch.setattr(main_window_module, "QMessageBox", _FakeSourceChooser)
    return calls


def _install_fake_text_input_dialog(
    monkeypatch: pytest.MonkeyPatch, accepted: bool = True, edited_text: str | None = None
) -> list:
    """Stands in for the real PatchTextInputDialog -- same reasoning as
    `_install_fake_selection_dialog` above: a real one would block forever
    in a modal event loop under the offscreen platform."""
    constructed: list = []

    class _FakeTextInputDialog:
        def __init__(self, clipboard_text: str, parent=None) -> None:
            self.clipboard_text = clipboard_text
            constructed.append(self)

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

        def patch_text(self) -> str:
            return self.clipboard_text if edited_text is None else edited_text

    monkeypatch.setattr(main_window_module, "PatchTextInputDialog", _FakeTextInputDialog)
    return constructed


def _setup_repo_with_two_committed_files(tmp_path: Path) -> tuple[Path, git.Repo]:
    repo_path = tmp_path / "repo"
    repo = _init_real_repo(repo_path)
    (repo_path / "a.txt").write_text("original a\n")
    (repo_path / "b.txt").write_text("original b\n")
    repo.index.add(["a.txt", "b.txt"])
    repo.index.commit("initial commit")
    return repo_path, repo


def test_apply_patch_action_present_only_on_repo_root_menu(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path, repo = _setup_repo_with_two_committed_files(tmp_path)
        (repo_path / "a.txt").write_text("changed\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        repo_index = window._tree_view.find_repo_index(repo_path)
        change = next(c for c in changes if c.path == Path("a.txt"))
        file_index = MainWindow._find_tree_index(window._tree_view.model(), repo_path, change)
        assert repo_index.isValid()
        assert file_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)

        repo_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(repo_index).center())
        assert any(a.text() == "Apply patch..." for a in repo_menus[0].actions())

        file_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(file_index).center())
        assert not any(a.text() == "Apply patch..." for a in file_menus[0].actions())
    finally:
        window.close()


def test_apply_patch_action_absent_on_non_repo_root_folder_menu(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "sub").mkdir()
        (repo_path / "sub" / "tracked.txt").write_text("tracked\n")
        repo.index.add(["sub/tracked.txt"])
        repo.index.commit("add sub/tracked.txt")
        (repo_path / "sub" / "new_file.txt").write_text("brand new\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        folder_index = _find_folder_index(window._tree_view.model(), repo_path / "sub")
        assert folder_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)

        folder_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(folder_index).center())
        assert not any(a.text() == "Apply patch..." for a in folder_menus[0].actions())
    finally:
        window.close()


def test_apply_patch_from_file_applies_only_the_selected_files_and_refreshes(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path, repo = _setup_repo_with_two_committed_files(tmp_path)

        (repo_path / "a.txt").write_text("changed a\n")
        (repo_path / "b.txt").write_text("changed b\n")
        patch_text = repo.git.diff("--no-color", "HEAD")
        repo.git.checkout("--", "a.txt", "b.txt")

        repository = Repository(path=repo_path, name="repo", branch_status=_BRANCH, changes=[])
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        patch_file = tmp_path / "the.patch"
        patch_file.write_text(patch_text)
        monkeypatch.setattr(
            main_window_module,
            "QFileDialog",
            SimpleNamespace(getOpenFileName=lambda *a, **k: (str(patch_file), "")),
        )
        chooser_calls = _install_fake_source_chooser(monkeypatch, click_target="file")
        _install_fake_selection_dialog(monkeypatch, selected_paths=[Path("a.txt")])
        refreshed: list = []
        monkeypatch.setattr(window, "_on_refresh_repo", lambda path: refreshed.append(path))

        window._on_apply_patch_for_repo(str(repo_path))

        assert (repo_path / "a.txt").read_text() == "changed a\n"
        # Not selected in the dialog -- must stay untouched even though it
        # was part of the same patch text.
        assert (repo_path / "b.txt").read_text() == "original b\n"
        assert refreshed == [repo_path]
        assert len(chooser_calls["information"]) == 1
    finally:
        window.close()


def test_apply_patch_from_clipboard_reads_editable_text_and_applies_it(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path, repo = _setup_repo_with_two_committed_files(tmp_path)

        (repo_path / "a.txt").write_text("changed a\n")
        patch_text = repo.git.diff("--no-color", "HEAD", "--", "a.txt")
        repo.git.checkout("--", "a.txt")

        repository = Repository(path=repo_path, name="repo", branch_status=_BRANCH, changes=[])
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        QGuiApplication.clipboard().setText(patch_text)
        _install_fake_source_chooser(monkeypatch, click_target="clipboard")
        text_input_dialogs = _install_fake_text_input_dialog(monkeypatch)
        _install_fake_selection_dialog(monkeypatch)
        monkeypatch.setattr(window, "_on_refresh_repo", lambda path: None)

        window._on_apply_patch_for_repo(str(repo_path))

        assert (repo_path / "a.txt").read_text() == "changed a\n"
        assert text_input_dialogs[0].clipboard_text == patch_text
    finally:
        window.close()


def test_apply_patch_cancel_at_source_chooser_never_opens_file_picker_or_parses(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path, _repo = _setup_repo_with_two_committed_files(tmp_path)
        repository = Repository(path=repo_path, name="repo", branch_status=_BRANCH, changes=[])
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        _install_fake_source_chooser(monkeypatch, click_target=None)

        def _fail_if_file_picker_opened(*_args, **_kwargs):
            raise AssertionError("file picker must never open when the source chooser is cancelled")

        monkeypatch.setattr(
            main_window_module,
            "QFileDialog",
            SimpleNamespace(getOpenFileName=_fail_if_file_picker_opened),
        )
        selection_dialogs = _install_fake_selection_dialog(monkeypatch)

        window._on_apply_patch_for_repo(str(repo_path))

        assert selection_dialogs == []
    finally:
        window.close()


def test_apply_patch_empty_parse_result_shows_info_and_never_opens_selection_dialog(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path, _repo = _setup_repo_with_two_committed_files(tmp_path)
        repository = Repository(path=repo_path, name="repo", branch_status=_BRANCH, changes=[])
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        garbage_file = tmp_path / "garbage.patch"
        garbage_file.write_text("not a patch at all\n")
        monkeypatch.setattr(
            main_window_module,
            "QFileDialog",
            SimpleNamespace(getOpenFileName=lambda *a, **k: (str(garbage_file), "")),
        )
        chooser_calls = _install_fake_source_chooser(monkeypatch, click_target="file")
        selection_dialogs = _install_fake_selection_dialog(monkeypatch)

        window._on_apply_patch_for_repo(str(repo_path))

        assert selection_dialogs == []
        assert len(chooser_calls["information"]) == 1
    finally:
        window.close()


def test_apply_patch_failure_shows_critical_and_never_refreshes(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path, repo = _setup_repo_with_two_committed_files(tmp_path)
        # A patch built against content the working tree no longer has --
        # the dry-run --check must fail, so apply_patch raises.
        (repo_path / "a.txt").write_text("some content the patch will expect\n")
        stale_patch = repo.git.diff("--no-color", "HEAD", "--", "a.txt")
        repo.git.checkout("--", "a.txt")
        (repo_path / "a.txt").write_text("a completely different diverged version\n")

        repository = Repository(path=repo_path, name="repo", branch_status=_BRANCH, changes=[])
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        patch_file = tmp_path / "stale.patch"
        patch_file.write_text(stale_patch)
        monkeypatch.setattr(
            main_window_module,
            "QFileDialog",
            SimpleNamespace(getOpenFileName=lambda *a, **k: (str(patch_file), "")),
        )
        chooser_calls = _install_fake_source_chooser(monkeypatch, click_target="file")
        _install_fake_selection_dialog(monkeypatch)
        refreshed: list = []
        monkeypatch.setattr(window, "_on_refresh_repo", lambda path: refreshed.append(path))

        window._on_apply_patch_for_repo(str(repo_path))

        assert (repo_path / "a.txt").read_text() == "a completely different diverged version\n"
        assert refreshed == []
        assert len(chooser_calls["critical"]) == 1
    finally:
        window.close()


def test_collapse_and_expand_all_folders_buttons_toggle_real_tree_rows(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two icon buttons next to the filter box (collapse/expand all
    folders) must actually walk the tree, not just exist and be connected to
    *some* slot -- this repo has shipped dead menu items before. Checks a
    NESTED folder's isExpanded(), not just a top-level repo row, since a
    naive implementation could stop after one level."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        changes = [FileChange(path=Path("src/pkg/nested/deep.py"), change_type=ChangeType.MODIFIED)]
        workspace = Workspace(root_path=tmp_path, repositories=[_repo("repo1", changes)])
        window._tree_view.set_workspace(workspace)

        model = window._tree_view._model
        nested_folder_index = _find_folder_index(model, Path("/repos/repo1/src/pkg/nested"))
        assert nested_folder_index.isValid()
        proxy_index = window._tree_view._proxy.mapFromSource(nested_folder_index)

        # set_workspace() already expandAll()'d the fresh tree -- confirm the
        # nested row starts expanded so the collapse click below is a real
        # state change, not a no-op that would pass trivially.
        assert window._tree_view.isExpanded(proxy_index) is True

        window._collapse_all_folders_button.click()
        assert window._tree_view.isExpanded(proxy_index) is False

        window._expand_all_folders_button.click()
        assert window._tree_view.isExpanded(proxy_index) is True
    finally:
        window.close()


def _worktree(name: str, changes: list[FileChange], parent_path: Path) -> Repository:
    return Repository(
        path=parent_path / ".worktrees" / name,
        name=name,
        branch_status=_BRANCH,
        changes=changes,
        logical_parent_path=parent_path,
    )


def test_hide_empty_worktrees_checkbox_toggles_changeless_worktree_visibility(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """"Hide empty worktrees" is the worktree-specific counterpart to "Hide
    repos without changes" (F35, which deliberately exempts every worktree --
    see f61bf6b/1c278f2). Default (unchecked) must preserve today's behavior:
    every worktree visible regardless of changes. Checking it must hide only
    the changeless worktree, update the tree immediately with no rescan, and
    leave regular (non-worktree) repos alone -- even a changeless one, since
    that's still exclusively F35's job."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        repo_with_changes = _repo(
            "repo_with_changes", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]
        )
        empty_repo = _repo("empty_repo", [])
        clean_worktree = _worktree("clean-wt", [], repo_with_changes.path)
        dirty_worktree = _worktree(
            "dirty-wt",
            [FileChange(path=Path("b.py"), change_type=ChangeType.MODIFIED)],
            repo_with_changes.path,
        )
        window._workspace = Workspace(
            root_path=tmp_path,
            repositories=[repo_with_changes, empty_repo, clean_worktree, dirty_worktree],
        )
        window._refresh_display()
        model = window._tree_view._model

        assert window._hide_changeless_worktrees_checkbox.isChecked() is False
        assert _repo_row_present(model, clean_worktree.path) is True
        assert _repo_row_present(model, dirty_worktree.path) is True
        assert _repo_row_present(model, empty_repo.path) is True

        window._hide_changeless_worktrees_checkbox.click()

        assert window._hide_changeless_worktrees_checkbox.isChecked() is True
        assert _repo_row_present(model, clean_worktree.path) is False
        assert _repo_row_present(model, dirty_worktree.path) is True
        # Untouched: "Hide repos without changes" is off, so a changeless
        # regular repo is not this checkbox's concern.
        assert _repo_row_present(model, empty_repo.path) is True

        window._hide_changeless_worktrees_checkbox.click()

        assert window._hide_changeless_worktrees_checkbox.isChecked() is False
        assert _repo_row_present(model, clean_worktree.path) is True
    finally:
        window.close()


def test_hide_empty_worktrees_checkbox_composes_with_hide_repos_without_changes(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two settings act on disjoint sets of rows (worktrees vs. regular
    repos), so all four on/off combinations must compose without one
    fighting the other."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        repo_with_changes = _repo(
            "repo_with_changes", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]
        )
        empty_repo = _repo("empty_repo", [])
        clean_worktree = _worktree("clean-wt", [], repo_with_changes.path)
        window._workspace = Workspace(
            root_path=tmp_path,
            repositories=[repo_with_changes, empty_repo, clean_worktree],
        )

        # hide_repos_without_changes ON, hide_changeless_worktrees OFF (F35's
        # existing, unmodified behavior): worktree stays, empty regular repo
        # is hidden.
        window._hide_empty_repos_action.setChecked(True)
        model = window._tree_view._model
        assert _repo_row_present(model, clean_worktree.path) is True
        assert _repo_row_present(model, empty_repo.path) is False

        # Both ON: both kinds of changeless rows are hidden.
        window._hide_changeless_worktrees_checkbox.click()
        assert _repo_row_present(model, clean_worktree.path) is False
        assert _repo_row_present(model, empty_repo.path) is False

        # hide_repos_without_changes OFF, hide_changeless_worktrees ON: only
        # the worktree is hidden.
        window._hide_empty_repos_action.setChecked(False)
        assert _repo_row_present(model, clean_worktree.path) is False
        assert _repo_row_present(model, empty_repo.path) is True
    finally:
        window.close()


def test_hide_empty_worktrees_checkbox_persists_across_restart(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    assert window._hide_changeless_worktrees_checkbox.isChecked() is False  # default
    window._hide_changeless_worktrees_checkbox.setChecked(True)
    window.close()

    window2 = MainWindow()
    try:
        assert window2._hide_changeless_worktrees_checkbox.isChecked() is True
    finally:
        window2.close()


# ---------------------------------------------------------------------------
# "Show stashes..." repo-root context-menu action
# ---------------------------------------------------------------------------


def test_show_stashes_action_present_only_on_repo_root_menu(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path, repo = _setup_repo_with_two_committed_files(tmp_path)
        (repo_path / "a.txt").write_text("changed\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        repo_index = window._tree_view.find_repo_index(repo_path)
        change = next(c for c in changes if c.path == Path("a.txt"))
        file_index = MainWindow._find_tree_index(window._tree_view.model(), repo_path, change)
        assert repo_index.isValid()
        assert file_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)

        repo_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(repo_index).center())
        assert any(a.text() == "Show stashes..." for a in repo_menus[0].actions())

        file_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(file_index).center())
        assert not any(a.text() == "Show stashes..." for a in file_menus[0].actions())
    finally:
        window.close()


def test_show_stashes_action_absent_on_non_repo_root_folder_menu(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "sub").mkdir()
        (repo_path / "sub" / "tracked.txt").write_text("tracked\n")
        repo.index.add(["sub/tracked.txt"])
        repo.index.commit("add sub/tracked.txt")
        (repo_path / "sub" / "new_file.txt").write_text("brand new\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        folder_index = _find_folder_index(window._tree_view.model(), repo_path / "sub")
        assert folder_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)

        folder_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(folder_index).center())
        assert not any(a.text() == "Show stashes..." for a in folder_menus[0].actions())
    finally:
        window.close()


def _install_fake_stashes_dialog(monkeypatch: pytest.MonkeyPatch, restored: bool) -> list:
    """Stands in for the real StashesDialog -- a real one would block in a
    modal event loop forever under the offscreen platform (same reasoning as
    `_install_fake_selection_dialog` above)."""
    constructed: list = []

    class _FakeStashesDialog:
        def __init__(self, repo_path, parent=None) -> None:
            self.repo_path = repo_path
            self.restored = restored
            constructed.append(self)

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(main_window_module, "StashesDialog", _FakeStashesDialog)
    return constructed


def test_show_stashes_refreshes_the_repo_when_dialog_reports_a_restore(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path, _repo = _setup_repo_with_two_committed_files(tmp_path)
        constructed = _install_fake_stashes_dialog(monkeypatch, restored=True)
        refreshed: list = []
        monkeypatch.setattr(
            window, "_on_refresh_repo", lambda path: refreshed.append(path)
        )

        window._on_show_stashes_for_repo(str(repo_path))

        assert len(constructed) == 1
        assert refreshed == [repo_path]
    finally:
        window.close()


def test_show_stashes_does_not_refresh_when_dialog_reports_no_restore(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path, _repo = _setup_repo_with_two_committed_files(tmp_path)
        _install_fake_stashes_dialog(monkeypatch, restored=False)
        refreshed: list = []
        monkeypatch.setattr(
            window, "_on_refresh_repo", lambda path: refreshed.append(path)
        )

        window._on_show_stashes_for_repo(str(repo_path))

        assert refreshed == []
    finally:
        window.close()


def test_error_indicator_hidden_on_a_fresh_window(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        window.show()
        assert window._error_indicator_button.isVisible() is False
    finally:
        window.close()


def test_report_error_shows_indicator_toast_and_tooltip(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        window.show()
        window._report_error("boom")

        assert window._error_indicator_button.isVisible() is True
        assert "1" in window._error_indicator_button.text()
        assert "error" in window._error_indicator_button.text().lower()
        assert "boom" in window._error_indicator_button.toolTip()
        assert "boom" in window.statusBar().currentMessage()

        window._report_error("second boom")

        assert "2" in window._error_indicator_button.text()
        assert "errors" in window._error_indicator_button.text().lower()
        assert "second boom" in window._error_indicator_button.toolTip()
    finally:
        window.close()


def test_report_error_logs_exactly_once_per_call(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        window._report_error("only once")

        assert applog.error_count() == 1
    finally:
        window.close()


# ---------------------------------------------------------------------------
# "File History…" entry points: folder right-click, changed-file
# right-click, and the View-menu action driven by tree selection.
# ---------------------------------------------------------------------------


def _install_fake_file_history_dialog(monkeypatch: pytest.MonkeyPatch) -> list:
    """Stands in for the real FileHistoryDialog -- a real one kicks off a
    background commits worker from __init__, and .exec() would block in a
    modal event loop forever under the offscreen platform (same reasoning as
    _install_fake_selection_dialog above). Capturing the constructor
    arguments MainWindow passes proves the *wiring* -- which repo/folder/
    initial_file each entry point resolves -- without re-testing the dialog's
    own behavior, already covered by tests/gui/test_file_history_dialog.py.
    """
    constructed: list = []

    class _FakeFileHistoryDialog:
        def __init__(
            self, repo_path, folder_path, parent=None, initial_file=None, **_kwargs
        ) -> None:
            self.repo_path = repo_path
            self.folder_path = folder_path
            self.parent = parent
            self.initial_file = initial_file
            constructed.append(self)

        def exec(self) -> int:
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(main_window_module, "FileHistoryDialog", _FakeFileHistoryDialog)
    return constructed


def test_file_history_action_on_non_root_folder_menu_resolves_owning_repo(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves both that File History is offered on a plain (non-root) folder
    -- unlike "Show Log", which is repo-root-only -- and that the handler
    passes the *repo root* to FileHistoryDialog, not the clicked subfolder:
    GitRepoAdapter.__init__ has no search_parent_directories, so a subfolder
    would raise InvalidGitRepositoryError if it ever reached the adapter."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "sub").mkdir()
        (repo_path / "sub" / "tracked.txt").write_text("tracked\n")
        repo.index.add(["sub/tracked.txt"])
        repo.index.commit("add sub/tracked.txt")
        (repo_path / "sub" / "new_file.txt").write_text("brand new\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        folder_index = _find_folder_index(window._tree_view.model(), repo_path / "sub")
        assert folder_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)
        captured_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(folder_index).center())
        assert len(captured_menus) == 1
        assert any(a.text() == "File History…" for a in captured_menus[0].actions())

        constructed = _install_fake_file_history_dialog(monkeypatch)
        action = next(a for a in captured_menus[0].actions() if a.text() == "File History…")
        action.trigger()

        assert len(constructed) == 1
        assert constructed[0].repo_path == repo_path
        assert constructed[0].folder_path == repo_path / "sub"
        assert constructed[0].initial_file is None
    finally:
        window.close()


def test_file_history_action_on_changed_file_menu_passes_initial_file(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "sub").mkdir()
        (repo_path / "sub" / "tracked.txt").write_text("original\n")
        repo.index.add(["sub/tracked.txt"])
        repo.index.commit("initial commit")
        (repo_path / "sub" / "tracked.txt").write_text("changed\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        change = next(c for c in changes if c.path == Path("sub/tracked.txt"))
        file_index = MainWindow._find_tree_index(window._tree_view.model(), repo_path, change)
        assert file_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)
        captured_menus = _capture_menu(monkeypatch)
        window._on_tree_context_menu(window._tree_view.visualRect(file_index).center())
        assert len(captured_menus) == 1
        assert any(a.text() == "File History…" for a in captured_menus[0].actions())

        constructed = _install_fake_file_history_dialog(monkeypatch)
        action = next(a for a in captured_menus[0].actions() if a.text() == "File History…")
        action.trigger()

        assert len(constructed) == 1
        assert constructed[0].repo_path == repo_path
        assert constructed[0].folder_path == repo_path / "sub"
        assert constructed[0].initial_file == Path("sub/tracked.txt")
    finally:
        window.close()


def test_file_history_view_menu_action_disabled_with_nothing_selected(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        assert window._file_history_action.isEnabled() is False
    finally:
        window.close()


def test_file_history_view_menu_action_enabled_once_folder_selected(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing selection tracking (_selected_change/_selected_repo_path)
    never fires for a folder-only selection, so this action's enablement
    must come from scope_changed instead -- proven here by selecting a
    folder row with no file ever selected."""
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "sub").mkdir()
        (repo_path / "sub" / "tracked.txt").write_text("tracked\n")
        repo.index.add(["sub/tracked.txt"])
        repo.index.commit("add sub/tracked.txt")
        (repo_path / "sub" / "new_file.txt").write_text("brand new\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        folder_index = _find_folder_index(window._tree_view.model(), repo_path / "sub")
        assert folder_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)
        assert window._file_history_action.isEnabled() is False

        window._tree_view.setCurrentIndex(folder_index)
        assert window._file_history_action.isEnabled() is True

        constructed = _install_fake_file_history_dialog(monkeypatch)
        window._file_history_action.trigger()

        assert len(constructed) == 1
        assert constructed[0].repo_path == repo_path
        assert constructed[0].folder_path == repo_path / "sub"
    finally:
        window.close()


def test_ctrl_f_on_the_folder_tree_opens_file_history_and_spares_the_find_bar(
    qapp, isolated_settings: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl+F is not a free key: SideBySideView already binds it to the
    inline find bar, scoped to the right diff pane. This action's shortcut is
    scoped the same way to the tree, so both survive -- proven by pressing
    the real key in each widget and checking that only the focused one
    responds. A window-scoped shortcut would make Qt report an ambiguous
    activation and break the find bar instead.
    """
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module, "save_workspace", lambda workspace: None)
    window = MainWindow()
    try:
        repo_path = tmp_path / "repo"
        repo = _init_real_repo(repo_path)
        (repo_path / "sub").mkdir()
        (repo_path / "sub" / "tracked.txt").write_text("tracked\n")
        repo.index.add(["sub/tracked.txt"])
        repo.index.commit("add sub/tracked.txt")
        # The tree only renders folders that hold a change, so `sub` needs one
        # to be selectable at all.
        (repo_path / "sub" / "new_file.txt").write_text("brand new\n")

        changes = GitRepoAdapter(repo_path).list_changes()
        repository = Repository(
            path=repo_path, name="repo", branch_status=_BRANCH, changes=changes
        )
        window._on_workspace_ready(Workspace(root_path=tmp_path, repositories=[repository]))

        folder_index = _find_folder_index(window._tree_view.model(), repo_path / "sub")
        assert folder_index.isValid()

        window.show()
        QTest.qWaitForWindowExposed(window)

        constructed = _install_fake_file_history_dialog(monkeypatch)

        window._tree_view.setCurrentIndex(folder_index)
        window._tree_view.setFocus()
        QTest.keyClick(
            window._tree_view, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier
        )

        assert len(constructed) == 1
        assert constructed[0].repo_path == repo_path
        assert constructed[0].folder_path == repo_path / "sub"

        # Same key, focus in the diff pane: File History must not fire, or it
        # has stolen Ctrl+F from the find bar this pane owns.
        right_pane = window._diff_view._side_by_side._right
        right_pane.setFocus()
        QTest.keyClick(right_pane, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)

        assert len(constructed) == 1
    finally:
        window.close()


def test_show_error_log_dialog_lists_errors_and_clear_hides_indicator(
    qapp, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(MainWindow, "_start_scan", lambda self, *args, **kwargs: None)
    window = MainWindow()
    try:
        window.show()
        window._report_error("dialog boom")
        assert window._error_indicator_button.isVisible() is True

        dialog = ErrorLogDialog(window, on_cleared=window._refresh_error_indicator)
        try:
            assert dialog._list.count() == 1
            assert "dialog boom" in dialog._list.item(0).text()

            dialog._on_clear()

            assert dialog._list.count() == 0
            assert applog.error_count() == 0
            assert window._error_indicator_button.isVisible() is False
        finally:
            dialog.close()
    finally:
        window.close()
