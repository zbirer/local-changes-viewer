import os
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import git
import pytest
from PySide6.QtCore import QModelIndex, QSettings
from PySide6.QtGui import QGuiApplication, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

import local_changes_viewer.gui.main_window as main_window_module
import local_changes_viewer.gui.settings as settings_module
from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.gui.diff_view import diff_view_widget as diff_view_widget_module
from local_changes_viewer.gui.main_window import MainWindow
from local_changes_viewer.gui.workers.diff_worker import DiffWorker
from local_changes_viewer.gui.workspace_tree.tree_model import FOLDER_PATH_ROLE

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

    monkeypatch.setattr(main_window_module, "QMenu", _NonBlockingMenu)
    return captured


def _trigger_create_patch(menu: QMenu) -> None:
    action = next(a for a in menu.actions() if a.text() == "Create patch")
    action.trigger()


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
