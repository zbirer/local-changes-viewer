import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.gui.diff_view import diff_view_widget, side_by_side_view
from local_changes_viewer.gui.diff_view.diff_view_widget import DiffViewWidget
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


def _multi_run_diff() -> DiffResult:
    """Three well-separated substitutions inside a SINGLE hunk -- the
    `--unified=100000` reality `compute_diff` actually produces (see
    git_repo_adapter.py:273). Old and new behavior diverge on exactly this
    shape: hunk-based navigation would find only ONE target (this diff has
    one `@@`), change-run-based navigation must find three."""
    hunk = DiffHunk(
        old_start=1,
        old_count=9,
        new_start=1,
        new_count=9,
        lines=[
            DiffLine(DiffLineKind.REMOVED, 1, None, "old1"),
            DiffLine(DiffLineKind.ADDED, None, 1, "new1"),
            DiffLine(DiffLineKind.CONTEXT, 2, 2, "same-a"),
            DiffLine(DiffLineKind.CONTEXT, 3, 3, "same-b"),
            DiffLine(DiffLineKind.CONTEXT, 4, 4, "same-c"),
            DiffLine(DiffLineKind.REMOVED, 5, None, "old2"),
            DiffLine(DiffLineKind.ADDED, None, 5, "new2"),
            DiffLine(DiffLineKind.CONTEXT, 6, 6, "same-d"),
            DiffLine(DiffLineKind.CONTEXT, 7, 7, "same-e"),
            DiffLine(DiffLineKind.CONTEXT, 8, 8, "same-f"),
            DiffLine(DiffLineKind.REMOVED, 9, None, "old3"),
            DiffLine(DiffLineKind.ADDED, None, 9, "new3"),
        ],
    )
    return DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[hunk])


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


def test_entering_edit_mode_expands_left_pane_to_full_original_source(
    qapp, tmp_path: Path
) -> None:
    """`_diff_with_substitution`'s single hunk spans old lines 5-7 with no
    context before it, so the reconstructed original is 7 lines -- 1-4
    blank (the diff never mentions them), 5-7 the hunk's own lines -- and
    the gutter must report real line 7 for the last block, not a folded
    diff-row position."""
    view = _ready_view(tmp_path, "one\ntwo\nthree", diff=_diff_with_substitution())

    assert view.enter_edit_mode()

    assert view._left.sequential_line_numbers is True
    assert view._left.document().blockCount() == 7
    assert view._left._lineno_for_block(6) == 7


def test_exiting_edit_mode_restores_left_pane_folded_diff_rendering(
    qapp, tmp_path: Path
) -> None:
    view = _ready_view(tmp_path, "one\ntwo\nthree", diff=_diff_with_substitution())
    view.enter_edit_mode()

    view.exit_edit_mode()

    assert view._left.sequential_line_numbers is False
    assert view._left._lineno_for_block(0) == 5
    assert view._left._lineno_for_block(1) == 6
    assert view._left._lineno_for_block(2) == 7


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
# 7-8: Ctrl+G goto-line popup dialog.
#
# QInputDialog.getInt is a static-style call (QInputDialog.getInt(...)),
# resolved through the name `QInputDialog` in side_by_side_view's own
# module namespace -- so it's patched there (not on the real Qt class
# globally) via a plain SimpleNamespace stand-in exposing a fake getInt.
# ---------------------------------------------------------------------------


def _patch_goto_dialog(monkeypatch: pytest.MonkeyPatch, line: int, ok: bool) -> list[tuple]:
    calls: list[tuple] = []

    def fake_get_int(parent, title, label, value, min_value, max_value):
        calls.append((value, min_value, max_value))
        return line, ok

    monkeypatch.setattr(side_by_side_view, "QInputDialog", SimpleNamespace(getInt=fake_get_int))
    return calls


def test_ctrl_g_opens_dialog_with_full_line_range_and_jumps_on_accept(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _ready_view(tmp_path, "one\ntwo\nthree\nfour\nfive")
    assert view.enter_edit_mode()
    view._right.setFocus()
    calls = _patch_goto_dialog(monkeypatch, line=3, ok=True)

    QTest.keyClick(view._right, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)

    # value=1 (cursor starts at line 1), min=1, max=blockCount() -- the
    # dialog's own args do the out-of-range clamping, not our arithmetic.
    assert calls == [(1, 1, 5)]
    assert view._right.textCursor().blockNumber() == 2  # line 3, 0-indexed


def test_goto_dialog_cancel_leaves_cursor_untouched(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view = _ready_view(tmp_path, "one\ntwo\nthree\nfour\nfive")
    view.enter_edit_mode()
    view._right.setFocus()
    before_pos = view._right.textCursor().position()
    _patch_goto_dialog(monkeypatch, line=3, ok=False)  # ok=False == Cancel

    QTest.keyClick(view._right, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier)

    assert view._right.textCursor().position() == before_pos


# ---------------------------------------------------------------------------
# Cmd+S (Ctrl+S elsewhere) saves via the same path as the Save button.
# ---------------------------------------------------------------------------


def test_cmd_s_shortcut_saves_through_the_same_path_as_the_save_button(
    qapp, tmp_path: Path
) -> None:
    """Drives QKeySequence.StandardKey.Save (Cmd+S on macOS, Ctrl+S
    elsewhere) rather than calling save_edits() or clicking the button
    directly, to prove the shortcut actually reaches the real Save code
    path end to end -- including writing the real file on disk and
    preserving its original CRLF line ending, not a parallel
    reimplementation of save."""
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(b"one\r\ntwo\r\nthree")
    view = DiffViewWidget()
    view.set_diff(_empty_diff(), str(file_path), file_path)
    view.show()
    QTest.qWaitForWindowExposed(view)

    view._edit_button.setChecked(True)
    assert view._side_by_side.is_editing()

    right = view._side_by_side._right
    cursor = right.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    right.setTextCursor(cursor)
    QTest.keyClicks(right, "_edited")
    assert right.document().isModified()

    QTest.keySequence(view, QKeySequence.StandardKey.Save)

    assert not right.document().isModified()
    assert file_path.read_bytes() == b"one\r\ntwo\r\nthree_edited"


# ---------------------------------------------------------------------------
# Bug 1: declining "Discard edits?" when toggling Edit off must not destroy
# the buffer. Qt flips the button to unchecked before emitting toggled(False)
# -- setChecked(True) to put it back is a real transition Qt re-emits
# synchronously, which used to re-enter enter_edit_mode() and re-read the
# file from disk on top of the very buffer the user chose to keep.
# ---------------------------------------------------------------------------


def _patch_question_reply(monkeypatch: pytest.MonkeyPatch, reply) -> None:
    """QMessageBox.question is a static-style call resolved through the name
    `QMessageBox` in diff_view_widget's own module namespace (mirrors how
    _patch_goto_dialog above patches QInputDialog in side_by_side_view) --
    patched there, not on the real Qt class globally, so no real modal ever
    blocks the test."""
    monkeypatch.setattr(
        diff_view_widget,
        "QMessageBox",
        SimpleNamespace(question=lambda *a, **k: reply, StandardButton=QMessageBox.StandardButton),
    )


def test_declining_discard_on_edit_toggle_off_preserves_buffer(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("one\ntwo\nthree")
    widget = DiffViewWidget()
    widget.set_diff(_empty_diff(), str(file_path), file_path)
    widget.show()
    QTest.qWaitForWindowExposed(widget)

    widget._edit_button.setChecked(True)
    assert widget._side_by_side.is_editing()
    right = widget._side_by_side._right
    cursor = right.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    right.setTextCursor(cursor)
    QTest.keyClicks(right, "MARKER")
    assert widget._side_by_side.has_unsaved_edits()

    _patch_question_reply(monkeypatch, QMessageBox.StandardButton.No)

    # Real button click, not a direct call to _on_edit_toggled: the button's
    # own check-state is exactly what desynchronizes if the fix is wrong.
    QTest.mouseClick(widget._edit_button, Qt.MouseButton.LeftButton)

    assert widget._edit_button.isChecked(), "declining must leave Edit checked"
    assert widget._side_by_side.is_editing(), "declining must not exit edit mode"
    assert "MARKER" in right.toPlainText(), (
        "declining re-read the file from disk and destroyed the buffer"
    )
    assert widget._side_by_side.has_unsaved_edits()


# ---------------------------------------------------------------------------
# Bug 3: Save must not silently clobber a file that changed on disk since
# Edit began (no mtime/size comparison existed at all before this fix).
# ---------------------------------------------------------------------------


def test_save_with_externally_changed_file_prompts_and_declining_preserves_disk_and_buffer(
    qapp, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("one\ntwo\nthree")
    widget = DiffViewWidget()
    widget.set_diff(_empty_diff(), str(file_path), file_path)
    widget.show()
    QTest.qWaitForWindowExposed(widget)

    widget._edit_button.setChecked(True)
    right = widget._side_by_side._right
    cursor = right.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    right.setTextCursor(cursor)
    QTest.keyClicks(right, "_LOCAL")

    # Simulate a git pull / another editor / a background refresh rewriting
    # the file while Edit is open. A different size (not just a touch)
    # guarantees the fingerprint differs regardless of filesystem mtime
    # resolution.
    file_path.write_text("one\ntwo\nthree\nEXTERNAL_CHANGE")

    _patch_question_reply(monkeypatch, QMessageBox.StandardButton.No)

    QTest.mouseClick(widget._save_button, Qt.MouseButton.LeftButton)

    assert file_path.read_text() == "one\ntwo\nthree\nEXTERNAL_CHANGE", (
        "declining the overwrite must leave the externally-changed file untouched"
    )
    assert right.toPlainText() == "one\ntwo\nthree_LOCAL", "buffer must survive the decline"
    assert widget._side_by_side.has_unsaved_edits()


# ---------------------------------------------------------------------------
# Prev/Next change navigation: the change-run fix for the "--unified=100000
# collapses everything into one hunk" defect (see diff_pairing.change_runs).
# ---------------------------------------------------------------------------


def _ready_widget(tmp_path: Path, file_text: str, diff: DiffResult) -> DiffViewWidget:
    file_path = tmp_path / "sample.txt"
    file_path.write_text(file_text)
    widget = DiffViewWidget()
    widget.set_side_by_side(True)
    widget.set_diff(diff, str(file_path), file_path)
    widget.show()
    QTest.qWaitForWindowExposed(widget)
    return widget


def test_successive_next_change_clicks_reach_successive_changes_in_diff_mode(
    qapp, tmp_path: Path
) -> None:
    widget = _ready_widget(
        tmp_path,
        "new1\nsame-a\nsame-b\nsame-c\nnew2\nsame-d\nsame-e\nsame-f\nnew3",
        _multi_run_diff(),
    )
    left = widget._side_by_side._left

    QTest.mouseClick(widget._next_button, Qt.MouseButton.LeftButton)
    first_block = left.textCursor().blockNumber()
    QTest.mouseClick(widget._next_button, Qt.MouseButton.LeftButton)
    second_block = left.textCursor().blockNumber()
    QTest.mouseClick(widget._next_button, Qt.MouseButton.LeftButton)
    third_block = left.textCursor().blockNumber()

    assert first_block < second_block < third_block, (
        "each 'Next change' click must land on a later change than the last -- "
        "with the old hunk-based navigation all three clicks land on block 0, "
        "since this diff is a single `@@` hunk."
    )

    QTest.mouseClick(widget._prev_button, Qt.MouseButton.LeftButton)
    assert left.textCursor().blockNumber() == second_block


def test_next_change_in_edit_mode_lands_each_pane_on_its_own_real_line(
    qapp, tmp_path: Path
) -> None:
    widget = _ready_widget(
        tmp_path,
        "new1\nsame-a\nsame-b\nsame-c\nnew2\nsame-d\nsame-e\nsame-f\nnew3",
        _multi_run_diff(),
    )
    widget._edit_button.setChecked(True)
    assert widget._side_by_side.is_editing()
    left = widget._side_by_side._left
    right = widget._side_by_side._right

    QTest.mouseClick(widget._next_button, Qt.MouseButton.LeftButton)
    assert left.textCursor().blockNumber() == 0  # old_lineno 1
    assert right.textCursor().blockNumber() == 0  # new_lineno 1
    assert right.hasFocus(), "focus must stay in the editable right pane"

    QTest.mouseClick(widget._next_button, Qt.MouseButton.LeftButton)
    assert left.textCursor().blockNumber() == 4  # old_lineno 5
    assert right.textCursor().blockNumber() == 4  # new_lineno 5

    QTest.mouseClick(widget._next_button, Qt.MouseButton.LeftButton)
    assert left.textCursor().blockNumber() == 8  # old_lineno 9
    assert right.textCursor().blockNumber() == 8  # new_lineno 9


# ---------------------------------------------------------------------------
# Edit disabled (with explaining tooltip) for an already-committed diff.
# ---------------------------------------------------------------------------


def test_edit_disabled_with_explaining_tooltip_for_committed_diff(
    qapp, tmp_path: Path
) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("content")
    widget = DiffViewWidget()

    widget.set_diff(
        _empty_diff(),
        str(file_path),
        file_path,
        "This is an already-committed (not yet pushed) change, so the file "
        "on disk no longer matches this diff.",
    )

    assert not widget._edit_button.isEnabled()
    assert widget._edit_button.toolTip() == (
        "This is an already-committed (not yet pushed) change, so the file "
        "on disk no longer matches this diff."
    )


def test_edit_enabled_with_normal_tooltip_for_working_tree_diff(
    qapp, tmp_path: Path
) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("content")
    widget = DiffViewWidget()

    widget.set_diff(_empty_diff(), str(file_path), file_path, None)

    assert widget._edit_button.isEnabled()
    assert widget._edit_button.toolTip() == "Edit the file in place"
