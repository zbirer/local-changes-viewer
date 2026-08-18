import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import local_changes_viewer.gui.applog as applog
import local_changes_viewer.gui.file_history_dialog as file_history_dialog_module
from local_changes_viewer.core.domain.commit_log_entry import CommitLogEntry
from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.core.domain.file_change import ChangeType
from local_changes_viewer.core.domain.file_history import (
    FileHistoryCommit,
    FileHistoryResult,
    TrackedFile,
    TrackedFilesResult,
)
from local_changes_viewer.gui.file_history_dialog import FileHistoryDialog
from local_changes_viewer.gui.workers.file_history_diff_worker import FileHistoryDiffMode
from tests.gui.test_worktrees_dialog import DeferredPool, ImmediatePool


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class FakeAdapter:
    """Stands in for GitRepoAdapter across every File History call the
    dialog can make. Each behavior is injected via the constructor rather
    than hard-coded, and every call is recorded, so a single test can both
    drive the dialog and assert on exactly how many times (and with what
    arguments) the adapter was invoked -- e.g. proving Refresh re-lists the
    subtree once per click rather than once per keystroke.
    """

    def __init__(
        self,
        *,
        files_result: TrackedFilesResult | None = None,
        files_error: Exception | None = None,
        history_by_path: dict[Path, FileHistoryResult] | None = None,
        history_error: Exception | None = None,
        diff_result: DiffResult | None = None,
        diff_error: Exception | None = None,
        remote_url: str | None = None,
    ) -> None:
        self.files_result = files_result if files_result is not None else TrackedFilesResult(files=[])
        self.files_error = files_error
        self.history_by_path = history_by_path or {}
        self.history_error = history_error
        self.diff_result = diff_result
        self.diff_error = diff_error
        self.remote_url = remote_url
        self.list_tracked_files_calls: list[Path] = []
        self.get_file_history_calls: list[Path] = []
        self.get_commit_file_diff_calls: list[tuple] = []
        self.get_file_diff_against_disk_calls: list[tuple] = []

    def list_tracked_files(self, subtree: Path) -> TrackedFilesResult:
        self.list_tracked_files_calls.append(subtree)
        if self.files_error is not None:
            raise self.files_error
        return self.files_result

    def get_file_history(self, path: Path, limit: int = 10, cancel_token=None) -> FileHistoryResult:
        self.get_file_history_calls.append(path)
        if self.history_error is not None:
            raise self.history_error
        return self.history_by_path.get(path, FileHistoryResult())

    def get_commit_file_diff(self, hexsha, path_at_commit, renamed_from=None) -> DiffResult:
        self.get_commit_file_diff_calls.append((hexsha, path_at_commit, renamed_from))
        if self.diff_error is not None:
            raise self.diff_error
        return self.diff_result if self.diff_result is not None else DiffResult(old_ref="a", new_ref="b")

    def get_file_diff_against_disk(self, hexsha, path_at_commit, current_path, cancel_token=None) -> DiffResult:
        self.get_file_diff_against_disk_calls.append((hexsha, path_at_commit, current_path))
        if self.diff_error is not None:
            raise self.diff_error
        return self.diff_result if self.diff_result is not None else DiffResult(old_ref="a", new_ref="disk")

    def get_remote_url(self, name: str = "origin") -> str | None:
        return self.remote_url


def _tf(path_str: str, changed: bool = False) -> TrackedFile:
    return TrackedFile(path=Path(path_str), has_local_changes=changed)


def _commit_entry(
    hexsha: str,
    message: str,
    *,
    author: str = "Jane",
    when: datetime | None = None,
    change_type: ChangeType = ChangeType.MODIFIED,
    path: str = "a.txt",
    renamed_from: str | None = None,
    full_message: str | None = None,
) -> FileHistoryCommit:
    commit = CommitLogEntry(
        hexsha=hexsha,
        short_hexsha=hexsha[:8],
        message=message,
        committed_datetime=when or datetime(2026, 1, 1, 12, 0),
        full_message=full_message if full_message is not None else message,
        author=author,
    )
    return FileHistoryCommit(
        commit=commit,
        path_at_commit=Path(path),
        change_type=change_type,
        renamed_from=Path(renamed_from) if renamed_from is not None else None,
    )


def _diff_with_line(text: str) -> DiffResult:
    hunk = DiffHunk(
        old_start=1,
        old_count=0,
        new_start=1,
        new_count=1,
        lines=[DiffLine(kind=DiffLineKind.ADDED, old_lineno=None, new_lineno=1, text=text)],
    )
    return DiffResult(old_ref="old", new_ref="new", hunks=[hunk])


def _make_dialog(
    tmp_path: Path,
    fake: FakeAdapter,
    *,
    folder: Path | None = None,
    thread_pool=None,
    initial_file: Path | None = None,
) -> FileHistoryDialog:
    return FileHistoryDialog(
        tmp_path,
        folder if folder is not None else tmp_path,
        adapter_factory=lambda _p: fake,
        thread_pool=thread_pool if thread_pool is not None else ImmediatePool(),
        initial_file=initial_file,
    )


# ---------------------------------------------------------------------------
# Search box: char threshold, ranking, path matching, display cap, too-large.
# ---------------------------------------------------------------------------


def test_under_two_chars_shows_status_and_no_results(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(files_result=TrackedFilesResult(files=[_tf("a.txt")]))
    dialog = _make_dialog(tmp_path, fake)

    dialog._search_box.setText("a")

    assert dialog._results_list.count() == 0
    assert dialog._status_label.text() == "Type at least 2 characters to search"


def test_two_chars_filters_to_matching_files(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(files_result=TrackedFilesResult(files=[_tf("settings.py"), _tf("other.py")]))
    dialog = _make_dialog(tmp_path, fake)

    dialog._search_box.setText("se")

    assert dialog._results_list.count() == 1
    assert dialog._results_list.item(0).text() == str(tmp_path / "settings.py")


def test_no_match_shows_named_message(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(files_result=TrackedFilesResult(files=[_tf("settings.py")]))
    dialog = _make_dialog(tmp_path, fake)

    dialog._search_box.setText("zz")

    assert dialog._results_list.count() == 0
    assert dialog._status_label.text() == "No files match 'zz'"


def test_best_match_ordering_exact_then_prefix_then_substring(qapp, tmp_path: Path) -> None:
    files = [_tf("barfoo.py"), _tf("foobar.py"), _tf("foo")]
    fake = FakeAdapter(files_result=TrackedFilesResult(files=files))
    dialog = _make_dialog(tmp_path, fake)

    dialog._search_box.setText("foo")

    texts = [dialog._results_list.item(i).text() for i in range(dialog._results_list.count())]
    assert texts == [
        str(tmp_path / "foo"),
        str(tmp_path / "foobar.py"),
        str(tmp_path / "barfoo.py"),
    ]


def test_slash_in_query_switches_to_path_matching(qapp, tmp_path: Path) -> None:
    files = [_tf("src/settings.py"), _tf("other/settings.py"), _tf("settings.py")]
    fake = FakeAdapter(files_result=TrackedFilesResult(files=files))
    dialog = _make_dialog(tmp_path, fake)

    dialog._search_box.setText("src/se")

    texts = [dialog._results_list.item(i).text() for i in range(dialog._results_list.count())]
    assert texts == [str(tmp_path / "src" / "settings.py")]


def test_showing_n_of_m_label_when_matches_exceed_display_cap(qapp, tmp_path: Path) -> None:
    files = [_tf(f"file{i}.py") for i in range(15)]
    fake = FakeAdapter(files_result=TrackedFilesResult(files=files))
    dialog = _make_dialog(tmp_path, fake)

    dialog._search_box.setText("file")

    assert dialog._results_list.count() == 10
    assert "showing 10 of 15" in dialog._status_label.text()


def test_too_large_subtree_shows_message_instead_of_list(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(files_result=TrackedFilesResult(too_large=True))
    dialog = _make_dialog(tmp_path, fake)

    dialog._search_box.setText("anything")

    assert dialog._results_list.count() == 0
    assert "too many tracked files" in dialog._status_label.text()


def test_dot_icon_and_tooltip_on_changed_files(qapp, tmp_path: Path) -> None:
    files = [_tf("changed.py", changed=True), _tf("clean.py", changed=False)]
    fake = FakeAdapter(files_result=TrackedFilesResult(files=files))
    dialog = _make_dialog(tmp_path, fake)

    dialog._search_box.setText("py")

    items = [dialog._results_list.item(i) for i in range(dialog._results_list.count())]
    changed_item = next(i for i in items if "changed.py" in i.text())
    clean_item = next(i for i in items if "clean.py" in i.text())

    assert not changed_item.icon().isNull()
    assert "Has uncommitted changes" in changed_item.toolTip()
    assert clean_item.icon().isNull()


# ---------------------------------------------------------------------------
# Selecting a file loads its commit history.
# ---------------------------------------------------------------------------


def test_clicking_result_row_populates_commit_table(qapp, tmp_path: Path) -> None:
    entry = _commit_entry("aaaa1111", "Fix bug", path="settings.py")
    fake = FakeAdapter(
        files_result=TrackedFilesResult(files=[_tf("settings.py")]),
        history_by_path={Path("settings.py"): FileHistoryResult(entries=[entry], current_path=Path("settings.py"))},
    )
    dialog = _make_dialog(tmp_path, fake)
    dialog._search_box.setText("sett")
    item = dialog._results_list.item(0)

    dialog._results_list.itemClicked.emit(item)

    assert dialog._commit_table.rowCount() == 1
    assert dialog._commit_table.item(0, 2).text() == "Fix bug"


def test_refresh_relists_subtree_once_per_click_not_per_keystroke(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(files_result=TrackedFilesResult(files=[_tf("a.py")]))
    dialog = _make_dialog(tmp_path, fake)

    assert len(fake.list_tracked_files_calls) == 1  # the initial load

    for ch in "abc":
        dialog._search_box.setText(dialog._search_box.text() + ch)
    assert len(fake.list_tracked_files_calls) == 1  # typing never re-lists

    dialog._refresh_button.click()

    assert len(fake.list_tracked_files_calls) == 2


def test_initial_file_populates_commit_table_without_any_click(qapp, tmp_path: Path) -> None:
    entry = _commit_entry("bbbb2222", "Initial commit", path="README.md")
    fake = FakeAdapter(
        files_result=TrackedFilesResult(files=[_tf("README.md")]),
        history_by_path={Path("README.md"): FileHistoryResult(entries=[entry], current_path=Path("README.md"))},
    )

    dialog = _make_dialog(tmp_path, fake, initial_file=Path("README.md"))

    assert dialog._commit_table.rowCount() == 1
    assert dialog._commit_table.item(0, 2).text() == "Initial commit"


def test_selecting_file_b_while_a_in_flight_never_shows_a_commits(qapp, tmp_path: Path) -> None:
    result_a = FileHistoryResult(entries=[_commit_entry("aaa", "A commit", path="a.txt")], current_path=Path("a.txt"))
    result_b = FileHistoryResult(entries=[_commit_entry("bbb", "B commit", path="b.txt")], current_path=Path("b.txt"))
    fake = FakeAdapter(history_by_path={Path("a.txt"): result_a, Path("b.txt"): result_b})
    pool = DeferredPool()
    dialog = _make_dialog(tmp_path, fake, thread_pool=pool)

    dialog._select_file(Path("a.txt"))
    token_a = dialog._commits_cancel_token
    worker_a = pool.pending[-1]

    dialog._select_file(Path("b.txt"))
    worker_b = pool.pending[-1]

    assert token_a.is_cancelled  # selecting B cancels A's outgoing request

    worker_b.run()
    assert dialog._commit_table.rowCount() == 1
    assert dialog._commit_table.item(0, 2).text() == "B commit"

    # A's result arrives late, after B has already rendered -- the dialog's
    # own stale-result guard (independent of cancellation, since a signal can
    # already be queued when cancel() fires) must drop it rather than
    # clobbering B's table with A's commits.
    worker_a.signals.succeeded.emit(result_a)

    assert dialog._commit_table.rowCount() == 1
    assert dialog._commit_table.item(0, 2).text() == "B commit"


# ---------------------------------------------------------------------------
# Diff: default mode, mode toggle, view toggle, "now at ..." label, live
# update on commit-row change.
# ---------------------------------------------------------------------------


def test_default_mode_is_changes_in_this_commit(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter()
    dialog = _make_dialog(tmp_path, fake)

    assert dialog._commit_mode_radio.isChecked()
    assert dialog._diff_mode is FileHistoryDiffMode.COMMIT


def test_now_at_label_shown_on_rename_in_disk_mode(qapp, tmp_path: Path) -> None:
    (tmp_path / "new_name.py").write_text("hello\n")
    entry = _commit_entry("cccc3333", "Old commit", path="old_name.py")
    result = FileHistoryResult(entries=[entry], current_path=Path("new_name.py"))
    fake = FakeAdapter(
        files_result=TrackedFilesResult(files=[_tf("old_name.py")]),
        history_by_path={Path("old_name.py"): result},
        diff_result=DiffResult(old_ref="a", new_ref="b"),
    )
    dialog = _make_dialog(tmp_path, fake)
    dialog._select_file(Path("old_name.py"))
    # isVisible() only reports correctly once the dialog is actually shown --
    # a not-yet-shown top-level widget reports every descendant as invisible
    # regardless of its own setVisible() calls (see test_diff_view.py's
    # _ready_view for the same offscreen-platform constraint).
    dialog.show()
    QTest.qWaitForWindowExposed(dialog)

    dialog._disk_mode_radio.setChecked(True)

    assert dialog._now_at_label.isVisible()
    assert str(tmp_path / "new_name.py") in dialog._now_at_label.text()


def test_selecting_a_different_commit_row_updates_the_diff(qapp, tmp_path: Path) -> None:
    entry_newest = _commit_entry("d2", "Second", path="a.py")
    entry_oldest = _commit_entry("d1", "First", path="a.py")
    result = FileHistoryResult(entries=[entry_newest, entry_oldest], current_path=Path("a.py"))
    diffs = {"d2": _diff_with_line("second version"), "d1": _diff_with_line("first version")}

    class DiffFake(FakeAdapter):
        def get_commit_file_diff(self, hexsha, path_at_commit, renamed_from=None) -> DiffResult:
            self.get_commit_file_diff_calls.append((hexsha, path_at_commit, renamed_from))
            return diffs[hexsha]

    fake = DiffFake(
        files_result=TrackedFilesResult(files=[_tf("a.py")]),
        history_by_path={Path("a.py"): result},
    )
    dialog = _make_dialog(tmp_path, fake)
    dialog._select_file(Path("a.py"))

    assert "second version" in dialog._unified_view.toPlainText()

    dialog._commit_table.selectRow(1)

    assert "first version" in dialog._unified_view.toPlainText()


def test_mode_b_on_unmodified_file_renders_empty_diff_not_error(qapp, tmp_path: Path) -> None:
    entry = _commit_entry("h1", "Subject", path="a.py")
    result = FileHistoryResult(entries=[entry], current_path=Path("a.py"))
    fake = FakeAdapter(
        files_result=TrackedFilesResult(files=[_tf("a.py")]),
        history_by_path={Path("a.py"): result},
        diff_result=DiffResult(old_ref="sha:a.py", new_ref="working tree"),  # zero hunks -- identical
    )
    dialog = _make_dialog(tmp_path, fake)
    dialog._select_file(Path("a.py"))

    dialog._disk_mode_radio.setChecked(True)

    assert dialog._diff_area_stack.currentWidget() is dialog._diff_panel_widget
    assert dialog._unified_view.toPlainText() == "(no changes)"


def test_view_toggle_switches_between_unified_and_side_by_side(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter()
    dialog = _make_dialog(tmp_path, fake)

    assert dialog._diff_stack.currentWidget() is dialog._unified_view

    dialog._view_toggle_button.setChecked(True)

    assert dialog._diff_stack.currentWidget() is dialog._side_by_side_view
    assert dialog._view_toggle_button.text() == "Unified"


# ---------------------------------------------------------------------------
# Hover popup.
# ---------------------------------------------------------------------------


def test_hover_popup_shows_full_message_on_any_column(qapp, tmp_path: Path) -> None:
    entry = _commit_entry(
        "e1", "Short subject", path="a.py", full_message="Short subject\n\nLonger body explaining why."
    )
    result = FileHistoryResult(entries=[entry], current_path=Path("a.py"))
    fake = FakeAdapter(files_result=TrackedFilesResult(files=[_tf("a.py")]), history_by_path={Path("a.py"): result})
    dialog = _make_dialog(tmp_path, fake)
    dialog._select_file(Path("a.py"))

    dialog._commit_table.cellEntered.emit(0, 1)  # "Author" column, not just the message column

    assert dialog._comment_popup.isVisible()
    assert dialog._comment_popup._label.text() == "Short subject\n\nLonger body explaining why."


def test_leave_event_on_commit_table_hides_hover_popup(qapp, tmp_path: Path) -> None:
    entry = _commit_entry("e2", "Subject", path="a.py")
    result = FileHistoryResult(entries=[entry], current_path=Path("a.py"))
    fake = FakeAdapter(files_result=TrackedFilesResult(files=[_tf("a.py")]), history_by_path={Path("a.py"): result})
    dialog = _make_dialog(tmp_path, fake)
    dialog._select_file(Path("a.py"))
    dialog._commit_table.cellEntered.emit(0, 0)
    assert dialog._comment_popup.isVisible()

    dialog.eventFilter(dialog._commit_table.viewport(), QEvent(QEvent.Type.Leave))

    assert not dialog._comment_popup.isVisible()


# ---------------------------------------------------------------------------
# Commit context menu.
# ---------------------------------------------------------------------------


def test_copy_commit_hash_and_copy_file_path_context_menu_actions(qapp, tmp_path: Path) -> None:
    entry = _commit_entry("ffff9999", "Subject", path="dir/a.py")
    result = FileHistoryResult(entries=[entry], current_path=Path("dir/a.py"))
    fake = FakeAdapter(
        files_result=TrackedFilesResult(files=[_tf("dir/a.py")]),
        history_by_path={Path("dir/a.py"): result},
    )
    dialog = _make_dialog(tmp_path, fake)
    dialog._select_file(Path("dir/a.py"))

    pos = dialog._commit_table.visualItemRect(dialog._commit_table.item(0, 0)).center()
    menu = dialog._build_commit_context_menu(pos)
    actions = {action.text(): action for action in menu.actions()}

    actions["Copy commit hash"].trigger()
    assert QGuiApplication.clipboard().text() == "ffff9999"

    actions["Copy file path"].trigger()
    assert QGuiApplication.clipboard().text() == str(tmp_path / "dir" / "a.py")


def test_github_action_disabled_without_remote(qapp, tmp_path: Path) -> None:
    entry = _commit_entry("aaaa0000", "Subject", path="a.py")
    result = FileHistoryResult(entries=[entry], current_path=Path("a.py"))
    fake = FakeAdapter(
        files_result=TrackedFilesResult(files=[_tf("a.py")]),
        history_by_path={Path("a.py"): result},
        remote_url=None,
    )
    dialog = _make_dialog(tmp_path, fake)
    dialog._select_file(Path("a.py"))

    pos = dialog._commit_table.visualItemRect(dialog._commit_table.item(0, 0)).center()
    menu = dialog._build_commit_context_menu(pos)
    action = next(a for a in menu.actions() if a.text() == "Open commit on GitHub")

    assert not action.isEnabled()
    assert action.toolTip() == "No GitHub remote configured for this repository"


def test_github_action_builds_correct_url_with_remote(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _commit_entry("bbbb1111", "Subject", path="a.py")
    result = FileHistoryResult(entries=[entry], current_path=Path("a.py"))
    fake = FakeAdapter(
        files_result=TrackedFilesResult(files=[_tf("a.py")]),
        history_by_path={Path("a.py"): result},
        remote_url="git@github.com:acme/widgets.git",
    )
    dialog = _make_dialog(tmp_path, fake)
    dialog._select_file(Path("a.py"))

    opened: list[str] = []
    monkeypatch.setattr(
        file_history_dialog_module,
        "QDesktopServices",
        SimpleNamespace(openUrl=lambda url: opened.append(url.toString())),
    )

    pos = dialog._commit_table.visualItemRect(dialog._commit_table.item(0, 0)).center()
    menu = dialog._build_commit_context_menu(pos)
    action = next(a for a in menu.actions() if a.text() == "Open commit on GitHub")
    assert action.isEnabled()

    action.trigger()

    assert opened == ["https://github.com/acme/widgets/commit/bbbb1111"]


# ---------------------------------------------------------------------------
# Keyboard / focus.
# ---------------------------------------------------------------------------


def test_search_box_has_focus_on_open(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter()
    dialog = _make_dialog(tmp_path, fake)
    dialog.show()
    QTest.qWaitForWindowExposed(dialog)

    assert dialog._search_box.hasFocus()


def test_down_key_from_search_box_moves_focus_into_results_list(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(files_result=TrackedFilesResult(files=[_tf("a.py"), _tf("b.py")]))
    dialog = _make_dialog(tmp_path, fake)
    dialog.show()
    QTest.qWaitForWindowExposed(dialog)
    dialog._search_box.setText("py")
    assert dialog._results_list.count() == 2

    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    consumed = dialog.eventFilter(dialog._search_box, event)

    assert consumed is True
    assert dialog._results_list.hasFocus()
    assert dialog._results_list.currentRow() == 0


def test_enter_on_results_row_loads_that_files_history(qapp, tmp_path: Path) -> None:
    entry = _commit_entry("g1", "Subject", path="a.py")
    result = FileHistoryResult(entries=[entry], current_path=Path("a.py"))
    fake = FakeAdapter(files_result=TrackedFilesResult(files=[_tf("a.py")]), history_by_path={Path("a.py"): result})
    dialog = _make_dialog(tmp_path, fake)
    dialog._search_box.setText("a.py")
    item = dialog._results_list.item(0)

    dialog._results_list.itemActivated.emit(item)

    assert dialog._commit_table.rowCount() == 1


def test_escape_closes_dialog(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter()
    dialog = _make_dialog(tmp_path, fake)
    dialog.show()

    QTest.keyClick(dialog, Qt.Key.Key_Escape)

    assert not dialog.isVisible()


# ---------------------------------------------------------------------------
# Empty states.
# ---------------------------------------------------------------------------


def test_empty_state_commit_table_before_file_picked(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter()
    dialog = _make_dialog(tmp_path, fake)

    assert dialog._commit_area_stack.currentWidget() is dialog._commit_status_label
    assert dialog._commit_status_label.text() == "Select a file to see its history"


def test_empty_state_diff_before_commit_picked(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter()
    dialog = _make_dialog(tmp_path, fake)

    assert dialog._diff_area_stack.currentWidget() is dialog._diff_status_label
    assert dialog._diff_status_label.text() == "Select a commit above to view its diff"


def test_no_commits_yet_for_file_shows_named_message(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(
        files_result=TrackedFilesResult(files=[_tf("new.py")]),
        history_by_path={Path("new.py"): FileHistoryResult(entries=[], current_path=Path("new.py"))},
    )
    dialog = _make_dialog(tmp_path, fake)

    dialog._select_file(Path("new.py"))

    assert dialog._commit_area_stack.currentWidget() is dialog._commit_status_label
    assert dialog._commit_status_label.text() == "No commits yet for this file"


# ---------------------------------------------------------------------------
# Worker errors: rendered inline in their own pane, logged at WARNING.
# ---------------------------------------------------------------------------


def test_files_worker_error_renders_inline_and_logs_warning(qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[tuple[str, applog.LogLevel]] = []
    monkeypatch.setattr(applog, "log", lambda message, level=applog.LogLevel.INFO: logged.append((message, level)))
    fake = FakeAdapter(files_error=RuntimeError("boom"))

    dialog = _make_dialog(tmp_path, fake)

    assert "Failed to list files" in dialog._status_label.text()
    assert logged and logged[-1][1] == applog.LogLevel.WARNING


def test_commits_worker_error_renders_inline_and_logs_warning(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged: list[tuple[str, applog.LogLevel]] = []
    monkeypatch.setattr(applog, "log", lambda message, level=applog.LogLevel.INFO: logged.append((message, level)))
    fake = FakeAdapter(files_result=TrackedFilesResult(files=[_tf("a.py")]), history_error=RuntimeError("history boom"))
    dialog = _make_dialog(tmp_path, fake)

    dialog._select_file(Path("a.py"))

    assert dialog._commit_area_stack.currentWidget() is dialog._commit_status_label
    assert "Failed to load commit history" in dialog._commit_status_label.text()
    assert logged and logged[-1][1] == applog.LogLevel.WARNING


def test_diff_worker_error_renders_inline_and_logs_warning(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged: list[tuple[str, applog.LogLevel]] = []
    monkeypatch.setattr(applog, "log", lambda message, level=applog.LogLevel.INFO: logged.append((message, level)))
    entry = _commit_entry("z1", "Subject", path="a.py")
    result = FileHistoryResult(entries=[entry], current_path=Path("a.py"))
    fake = FakeAdapter(
        files_result=TrackedFilesResult(files=[_tf("a.py")]),
        history_by_path={Path("a.py"): result},
        diff_error=RuntimeError("diff boom"),
    )
    dialog = _make_dialog(tmp_path, fake)

    dialog._select_file(Path("a.py"))

    assert dialog._diff_area_stack.currentWidget() is dialog._diff_status_label
    assert "Failed to load diff" in dialog._diff_status_label.text()
    assert logged and logged[-1][1] == applog.LogLevel.WARNING


# ---------------------------------------------------------------------------
# Cancel on close.
# ---------------------------------------------------------------------------


def test_closing_dialog_mid_fetch_cancels_worker_and_still_emits_finished(qapp, tmp_path: Path) -> None:
    fake = FakeAdapter(files_result=TrackedFilesResult(files=[_tf("a.py")]))
    pool = DeferredPool()
    dialog = _make_dialog(tmp_path, fake, thread_pool=pool)

    dialog._select_file(Path("a.py"))
    token = dialog._commits_cancel_token
    worker = pool.pending[-1]

    dialog.reject()

    assert token.is_cancelled

    finished_calls: list[bool] = []
    worker.signals.finished.connect(lambda: finished_calls.append(True))
    worker.run()

    assert finished_calls == [True]
