import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.gui.diff_view.side_by_side_view import SideBySideView


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _diff_with_substitution() -> DiffResult:
    """One hunk with a shared context line followed by two separate
    removed/added substitution rows -- the second row's removed line has
    no added counterpart, so the right pane's diff-mode line_numbers come
    out [50, 51, None]: non-sequential, and containing a None gap, both
    shapes edit mode's plain 1..N numbering could never produce. That
    contrast is exactly what the exit-edit-mode test below checks for."""
    hunk = DiffHunk(
        old_start=5,
        old_count=3,
        new_start=50,
        new_count=2,
        lines=[
            DiffLine(DiffLineKind.CONTEXT, 5, 50, "shared context"),
            DiffLine(DiffLineKind.REMOVED, 6, None, "old line"),
            DiffLine(DiffLineKind.ADDED, None, 51, "new line"),
            DiffLine(DiffLineKind.REMOVED, 7, None, "trailing removed line"),
        ],
    )
    return DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[hunk])


def _empty_diff() -> DiffResult:
    return DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[])


def _ready_view(tmp_path: Path, file_text: str, diff: DiffResult | None = None) -> SideBySideView:
    """A shown, exposed view wired to a real on-disk file as its edit
    target. Showing it (rather than leaving it unparented) matters: the
    Ctrl+F/Ctrl+G QShortcuts use WidgetWithChildrenShortcut context, which
    Qt resolves against the real focus widget, and offscreen-platform
    widgets only report focus correctly once actually shown."""
    file_path = tmp_path / "sample.txt"
    file_path.write_text(file_text)
    view = SideBySideView()
    view.set_diff(diff if diff is not None else _empty_diff(), str(file_path))
    view.set_file_target(file_path)
    view.show()
    QTest.qWaitForWindowExposed(view)
    return view


def _lineno_sequence(view: SideBySideView) -> list[int | None]:
    pane = view._right
    return [pane._lineno_for_block(i) for i in range(pane.document().blockCount())]


# ---------------------------------------------------------------------------
# 1-3: real/sequential line numbers in the edit pane.
# ---------------------------------------------------------------------------


def test_entering_edit_mode_shows_real_sequential_line_numbers(
    qapp, tmp_path: Path
) -> None:
    view = _ready_view(tmp_path, "one\ntwo\nthree\nfour")

    assert view.enter_edit_mode()

    assert view._right.sequential_line_numbers is True
    assert _lineno_sequence(view) == [1, 2, 3, 4]


def test_typing_a_new_line_keeps_gutter_sequential(qapp, tmp_path: Path) -> None:
    """The regression this guards: a line_numbers list snapshotted once at
    enter_edit_mode() would still read [1, 2, 3] after a 4th line is typed,
    since nothing repopulates it as the user edits."""
    view = _ready_view(tmp_path, "one\ntwo\nthree")
    view.enter_edit_mode()

    cursor = view._right.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    view._right.setTextCursor(cursor)
    QTest.keyClick(view._right, Qt.Key.Key_Return)
    QTest.keyClicks(view._right, "four")

    block_count = view._right.document().blockCount()
    assert block_count == 4
    assert view._right._lineno_for_block(block_count - 1) == block_count


def test_exiting_edit_mode_restores_diff_row_line_numbers(qapp, tmp_path: Path) -> None:
    view = _ready_view(tmp_path, "one\ntwo\nthree", diff=_diff_with_substitution())
    view.enter_edit_mode()

    view.exit_edit_mode()

    assert view._right.sequential_line_numbers is False
    assert view._right._lineno_for_block(0) == 50
    assert view._right._lineno_for_block(1) == 51
    assert view._right._lineno_for_block(2) is None


# ---------------------------------------------------------------------------
# 4-6, 9: Ctrl+F find bar.
# ---------------------------------------------------------------------------


def test_ctrl_f_shows_find_bar_only_in_edit_mode(qapp, tmp_path: Path) -> None:
    view = _ready_view(tmp_path, "one\ntwo\nthree")

    view._right.setFocus()
    QTest.keyClick(view._right, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert not view._bar.isVisible(), "Ctrl+F must be inert in read-only diff mode"

    assert view.enter_edit_mode()
    view._right.setFocus()
    QTest.keyClick(view._right, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert view._bar.isVisible()
    assert view._bar_mode == "find"


def test_find_next_advances_through_occurrences_and_wraps(qapp, tmp_path: Path) -> None:
    view = _ready_view(tmp_path, "one\nneedle_one\nthree\nfour\nneedle_two\nsix")
    view.enter_edit_mode()
    view._right.setFocus()
    QTest.keyClick(view._right, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)

    QTest.keyClicks(view._bar_input, "needle")
    first_match = view._right.textCursor().selectionStart()
    assert view._right.textCursor().selectedText() == "needle"

    QTest.keyClick(view._bar_input, Qt.Key.Key_Return)
    second_match = view._right.textCursor().selectionStart()
    assert second_match != first_match, "second find must advance to the next occurrence"

    QTest.keyClick(view._bar_input, Qt.Key.Key_Return)
    wrapped_match = view._right.textCursor().selectionStart()
    assert wrapped_match == first_match, "find must wrap back to the first occurrence"


def test_non_matching_search_gives_feedback_and_leaves_cursor_put(
    qapp, tmp_path: Path
) -> None:
    view = _ready_view(tmp_path, "one\ntwo\nthree")
    view.enter_edit_mode()
    view._right.setFocus()
    before_pos = view._right.textCursor().position()

    QTest.keyClick(view._right, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(view._bar_input, "zzz_nomatch_zzz")

    assert view._right.textCursor().position() == before_pos
    assert "B91C1C" in view._bar_input.styleSheet()


def test_escape_closes_bar_and_returns_focus_to_pane(qapp, tmp_path: Path) -> None:
    view = _ready_view(tmp_path, "one\ntwo\nthree")
    view.enter_edit_mode()
    view._right.setFocus()
    QTest.keyClick(view._right, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    assert view._bar.isVisible()

    QTest.keyClick(view._bar_input, Qt.Key.Key_Escape)

    assert not view._bar.isVisible()
    assert view._right.hasFocus()


# ---------------------------------------------------------------------------
# 7-8: Ctrl+G goto line bar.
# ---------------------------------------------------------------------------


def test_ctrl_g_shows_goto_bar_and_jumps_to_valid_line(qapp, tmp_path: Path) -> None:
    view = _ready_view(tmp_path, "one\ntwo\nthree\nfour\nfive")
    assert view.enter_edit_mode()
    view._right.setFocus()

    QTest.keyClick(view._right, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
    assert view._bar.isVisible()
    assert view._bar_mode == "goto"

    QTest.keyClicks(view._bar_input, "3")
    QTest.keyClick(view._bar_input, Qt.Key.Key_Return)

    assert view._right.textCursor().blockNumber() == 2  # line 3, 0-indexed
    assert not view._bar.isVisible()


def test_goto_out_of_range_line_clamps_instead_of_raising(qapp, tmp_path: Path) -> None:
    view = _ready_view(tmp_path, "one\ntwo\nthree\nfour\nfive")
    view.enter_edit_mode()
    view._right.setFocus()

    QTest.keyClick(view._right, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClicks(view._bar_input, "9999")
    QTest.keyClick(view._bar_input, Qt.Key.Key_Return)

    last_block = view._right.document().blockCount() - 1
    assert view._right.textCursor().blockNumber() == last_block
