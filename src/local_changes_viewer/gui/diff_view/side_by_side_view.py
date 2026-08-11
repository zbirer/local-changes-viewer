from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeySequence,
    QPainter,
    QShortcut,
    QTextCursor,
    QTextDocument,
    QTextFormat,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.diff import DiffLineKind, DiffResult
from local_changes_viewer.core.services.context_folding import FoldedRun, fold_context
from local_changes_viewer.core.services.diff_pairing import (
    ChangeRun,
    PairedLine,
    change_runs,
    pair_hunk_lines,
    reconstruct_old_file_lines,
)
from local_changes_viewer.core.services.file_info import detect_encoding, detect_line_ending
from local_changes_viewer.core.services.intraline_diff import intraline_ranges
from local_changes_viewer.gui.diff_view.syntax_highlighter import PygmentsHighlighter

_ENCODING_TO_CODEC = {
    "UTF-8": "utf-8",
    "UTF-8 (BOM)": "utf-8-sig",
    "Latin-1": "latin-1",
}

_LINE_BG = {
    DiffLineKind.ADDED: QColor("#DCFCE7"),
    DiffLineKind.REMOVED: QColor("#FEE2E2"),
}
_LINE_FG = {
    DiffLineKind.ADDED: QColor("#065F46"),
    DiffLineKind.REMOVED: QColor("#991B1B"),
}
_INTRALINE_BG = {
    DiffLineKind.ADDED: QColor("#86EFAC"),
    DiffLineKind.REMOVED: QColor("#FCA5A5"),
}


class _GutterWidget(QWidget):
    def __init__(self, pane: "_DiffPane") -> None:
        super().__init__(pane)
        self._pane = pane

    def sizeHint(self) -> QSize:
        return QSize(self._pane.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self._pane.paint_gutter(event)


class _DiffPane(QPlainTextEdit):
    def __init__(self, on_marker_click: Callable[[tuple[int, int]], None]) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont("Menlo", 12))
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.highlighter = PygmentsHighlighter(self.document())
        self.fold_keys: list[tuple[int, int] | None] = []
        self.line_numbers: list[int | None] = []
        # Edit mode turns this on: the gutter then paints block_number + 1
        # directly (see paint_gutter/_lineno_for_block) instead of indexing
        # `line_numbers`, because that list is a snapshot taken once and
        # would desync the moment the user inserts or deletes a line --
        # Qt's block count changes immediately but a snapshot list can't.
        self.sequential_line_numbers = False
        self._on_marker_click = on_marker_click
        self._line_numbers_visible = True
        self._gutter = _GutterWidget(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter_area)
        self._update_gutter_width()

    def mousePressEvent(self, event) -> None:
        block_number = self.cursorForPosition(event.pos()).blockNumber()
        if block_number < len(self.fold_keys):
            fold_key = self.fold_keys[block_number]
            if fold_key is not None:
                self._on_marker_click(fold_key)
                return
        super().mousePressEvent(event)

    def set_font_point_size(self, size: int) -> None:
        font = self.font()
        font.setPointSize(size)
        self.setFont(font)
        self._update_gutter_width()
        self._gutter.update()

    def set_line_numbers_visible(self, visible: bool) -> None:
        self._line_numbers_visible = visible
        self._gutter.setVisible(visible)
        self._update_gutter_width()

    def gutter_width(self) -> int:
        if not self._line_numbers_visible:
            return 0
        if self.sequential_line_numbers:
            # line_numbers holds stale diff-row numbers in edit mode (it is
            # never repopulated there), so width must come from the live
            # block count instead, same source paint_gutter itself uses.
            digits = len(str(max(self.blockCount(), 1)))
        else:
            digits = max((len(str(n)) for n in self.line_numbers if n is not None), default=1)
        digits = max(digits, 2)
        metrics = QFontMetrics(self.font())
        return metrics.horizontalAdvance("9") * digits + 10

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._gutter.setGeometry(
            QRect(rect.left(), rect.top(), self.gutter_width(), rect.height())
        )

    def _update_gutter_width(self) -> None:
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter_area(self, rect, dy) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def paint_gutter(self, event) -> None:
        painter = QPainter(self._gutter)
        painter.fillRect(event.rect(), QColor("#F3F4F6"))

        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())

        metrics = QFontMetrics(self.font())

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                lineno = self._lineno_for_block(block_number)
                if lineno is not None:
                    painter.setPen(QColor("#6B7280"))
                    painter.drawText(
                        0,
                        top,
                        self._gutter.width() - 5,
                        metrics.height(),
                        Qt.AlignmentFlag.AlignRight,
                        str(lineno),
                    )

            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1

    def _lineno_for_block(self, block_number: int) -> int | None:
        if self.sequential_line_numbers:
            # Edit mode always shows the real, whole file (never folded),
            # so the displayed number is simply the block's own position --
            # reading it straight off the document avoids the snapshot-list
            # desync described on `sequential_line_numbers` above.
            return block_number + 1
        if block_number < len(self.line_numbers):
            return self.line_numbers[block_number]
        return None


class _BarLineEdit(QLineEdit):
    """The find/goto bar's input, with key handling QLineEdit alone can't
    express: Escape to close the bar, and Shift+Enter for find-previous
    (the plain `returnPressed` signal carries no modifier information, so
    telling Enter and Shift+Enter apart needs this override)."""

    def __init__(
        self,
        on_escape: Callable[[], None],
        on_return: Callable[[], None],
        on_shift_return: Callable[[], None],
    ) -> None:
        super().__init__()
        self._on_escape = on_escape
        self._on_return = on_return
        self._on_shift_return = on_shift_return

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._on_escape()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._on_shift_return()
            else:
                self._on_return()
            return
        super().keyPressEvent(event)


class SideBySideView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._diff: DiffResult | None = None
        self._file_path: str | None = None
        self._abs_file_path: Path | None = None
        self._expanded_folds: set[tuple[int, int]] = set()
        self._change_runs: list[ChangeRun] = []
        self._change_run_rows: list[int] = []
        self._editing = False
        self._edit_codec = "utf-8"
        self._edit_line_ending = "LF"
        # (mtime_ns, size) of _abs_file_path as of the moment edit mode last
        # read it (or last wrote it -- see save_edits) -- see disk_stat() and
        # disk_changed_since_edit() below for the save-clobber guard this
        # backs.
        self._edit_disk_stat: tuple[int, int] | None = None
        self._left = _DiffPane(self._on_marker_click)
        self._right = _DiffPane(self._on_marker_click)
        self._syncing = False
        self._sync_scroll_enabled = True
        self._left.verticalScrollBar().valueChanged.connect(self._sync_vertical_from_left)
        self._right.verticalScrollBar().valueChanged.connect(self._sync_vertical_from_right)
        self._left.horizontalScrollBar().valueChanged.connect(self._sync_horizontal_from_left)
        self._right.horizontalScrollBar().valueChanged.connect(self._sync_horizontal_from_right)

        splitter = QSplitter()
        splitter.addWidget(self._left)
        splitter.addWidget(self._right)

        # Ctrl+F/Ctrl+G are parented to the right pane itself (not `self`)
        # with WidgetWithChildrenShortcut context, so Qt only routes them
        # here when the keyboard focus is inside that pane -- per spec,
        # these must be scoped to the edit pane, not the whole view. The
        # `_editing` check in each slot is still needed on top of that
        # because the right pane keeps accepting focus while read-only in
        # diff mode, where the context alone would otherwise let it fire.
        # Ctrl+F opens the inline find bar below; Ctrl+G opens a modal
        # QInputDialog (see _open_goto_bar) -- only what each key *opens*
        # differs, the scoping/guard mechanics are identical.
        self._find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self._right)
        self._find_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._find_shortcut.activated.connect(self._open_find_bar)
        self._goto_shortcut = QShortcut(QKeySequence("Ctrl+G"), self._right)
        self._goto_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._goto_shortcut.activated.connect(self._open_goto_bar)

        self._find_anchor: QTextCursor | None = None
        self._bar = QWidget()
        self._bar.setVisible(False)
        bar_layout = QHBoxLayout(self._bar)
        bar_layout.setContentsMargins(4, 2, 4, 2)
        self._bar_label = QLabel("Find:")
        self._bar_input = _BarLineEdit(
            on_escape=self._close_bar,
            on_return=self._on_bar_return,
            on_shift_return=self._on_bar_shift_return,
        )
        self._bar_prev_button = QPushButton("Prev")
        self._bar_next_button = QPushButton("Next")
        self._bar_close_button = QPushButton("✕")
        self._bar_close_button.setFixedWidth(24)
        bar_layout.addWidget(self._bar_label)
        bar_layout.addWidget(self._bar_input, 1)
        bar_layout.addWidget(self._bar_prev_button)
        bar_layout.addWidget(self._bar_next_button)
        bar_layout.addWidget(self._bar_close_button)
        self._bar_input.textChanged.connect(self._on_bar_text_changed)
        self._bar_next_button.clicked.connect(self._find_next)
        self._bar_prev_button.clicked.connect(self._find_prev)
        self._bar_close_button.clicked.connect(self._close_bar)

        # The find bar stays a non-modal inline widget, not QInputDialog: a
        # modal dialog would block the pane underneath it and is
        # effectively untestable (it steals the event loop instead of
        # participating in the same widget tree tests can drive with
        # QTest) -- and find benefits from the incremental-as-you-type
        # feedback a one-shot dialog can't give. Goto-line has neither
        # need (see _open_goto_bar), which is why only it uses a dialog.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter)
        layout.addWidget(self._bar)

    def set_sync_scroll(self, enabled: bool) -> None:
        self._sync_scroll_enabled = enabled

    def set_font_point_size(self, size: int) -> None:
        self._left.set_font_point_size(size)
        self._right.set_font_point_size(size)

    def set_line_numbers_visible(self, visible: bool) -> None:
        self._left.set_line_numbers_visible(visible)
        self._right.set_line_numbers_visible(visible)

    def _sync_vertical_from_left(self, value: int) -> None:
        if self._syncing or not self._sync_scroll_enabled:
            return
        self._syncing = True
        self._right.verticalScrollBar().setValue(value)
        self._syncing = False

    def _sync_vertical_from_right(self, value: int) -> None:
        if self._syncing or not self._sync_scroll_enabled:
            return
        self._syncing = True
        self._left.verticalScrollBar().setValue(value)
        self._syncing = False

    def _sync_horizontal_from_left(self, value: int) -> None:
        if self._syncing or not self._sync_scroll_enabled:
            return
        self._syncing = True
        self._right.horizontalScrollBar().setValue(value)
        self._syncing = False

    def _sync_horizontal_from_right(self, value: int) -> None:
        if self._syncing or not self._sync_scroll_enabled:
            return
        self._syncing = True
        self._left.horizontalScrollBar().setValue(value)
        self._syncing = False

    def _on_marker_click(self, fold_key: tuple[int, int]) -> None:
        self._expanded_folds.add(fold_key)
        self._rebuild()

    def set_diff(self, diff: DiffResult, file_path: str | None = None) -> None:
        self._diff = diff
        self._file_path = file_path
        self._expanded_folds = set()
        self._editing = False
        self._right.setReadOnly(True)
        self._reset_bar()
        self._rebuild()

    def set_file_target(self, abs_file_path: Path | None) -> None:
        self._abs_file_path = abs_file_path

    def file_target(self) -> Path | None:
        return self._abs_file_path

    def is_editing(self) -> bool:
        return self._editing

    def has_unsaved_edits(self) -> bool:
        return self._editing and self._right.document().isModified()

    def enter_edit_mode(self) -> bool:
        if self._editing:
            # Bug 1's second, independent guard layer: diff_view_widget's
            # blockSignals() stops the *spurious* re-toggle from the
            # "Discard edits?" No/Cancel path from ever reaching here, but
            # this no-op must also hold on its own -- any other future path
            # that re-enters edit mode while already editing must not
            # re-read the file from disk and clobber the live buffer either.
            return True
        if self._abs_file_path is None:
            return False
        try:
            raw = self._abs_file_path.read_bytes()
        except OSError:
            return False
        encoding = detect_encoding(raw)
        codec = _ENCODING_TO_CODEC.get(encoding)
        if codec is None:
            return False
        text = raw.decode(codec).replace("\r\n", "\n").replace("\r", "\n")
        self._edit_codec = codec
        self._edit_line_ending = detect_line_ending(raw)
        self._editing = True
        # Fingerprint the file as of this read so save_edits() can later tell
        # whether something else rewrote it underneath the buffer (Bug 3).
        self._edit_disk_stat = self._disk_stat()

        # The left pane gets the same whole-file treatment as the right so
        # the two scroll together like two plain files -- see the module
        # docstring-equivalent comment on `sequential_line_numbers` above.
        # `reconstruct_old_file_lines` derives the old side straight from
        # `self._diff`, since (unlike the right pane's live-on-disk text)
        # there is no separate "old file" to read off disk.
        old_lines = reconstruct_old_file_lines(self._diff) if self._diff is not None else []
        self._left.fold_keys = []
        self._left.setExtraSelections([])
        self._left.sequential_line_numbers = True
        self._left.setPlainText("\n".join(old_lines))
        self._left._update_gutter_width()
        self._left._gutter.update()

        self._right.fold_keys = []
        self._right.setExtraSelections([])
        # Set before setPlainText so the blockCountChanged it triggers
        # already recomputes gutter width against the sequential-mode
        # digit count, not the stale diff-row line_numbers list.
        self._right.sequential_line_numbers = True
        self._right.setPlainText(text)
        self._right.document().setModified(False)
        self._right.setReadOnly(False)
        self._right._update_gutter_width()
        self._right._gutter.update()

        # Expanding both panes to their whole files means the folded-diff
        # highlighting _rebuild() applied is gone too; re-derive it at each
        # pane's own real line number instead of the diff-row position that
        # highlighting was keyed to.
        self._highlight_edit_mode_panes(len(old_lines), self._right.document().blockCount())
        return True

    def _highlight_edit_mode_panes(self, left_line_count: int, right_line_count: int) -> None:
        """Rebuilds full-line REMOVED/ADDED highlighting for edit mode's
        whole-file panes, keyed by each line's real `old_lineno`/`new_lineno`
        rather than its diff-row position (which no longer exists once the
        panes are expanded). Intraline ranges are not carried over: a
        removed line and its added counterpart no longer sit on the same
        row once each pane scrolls independently, so there is no adjacent
        pair left to diff a substring against.
        """
        left_kinds: list[DiffLineKind | None] = [None] * left_line_count
        right_kinds: list[DiffLineKind | None] = [None] * right_line_count
        if self._diff is not None:
            for hunk in self._diff.hunks:
                for line in hunk.lines:
                    if line.kind is DiffLineKind.REMOVED and line.old_lineno is not None:
                        index = line.old_lineno - 1
                        if 0 <= index < len(left_kinds):
                            left_kinds[index] = DiffLineKind.REMOVED
                    elif line.kind is DiffLineKind.ADDED and line.new_lineno is not None:
                        index = line.new_lineno - 1
                        if 0 <= index < len(right_kinds):
                            right_kinds[index] = DiffLineKind.ADDED
        self._highlight(self._left, left_kinds, [[] for _ in left_kinds])
        self._highlight(self._right, right_kinds, [[] for _ in right_kinds])

    def exit_edit_mode(self) -> None:
        self._editing = False
        self._right.setReadOnly(True)
        self._reset_bar()
        self._rebuild()

    def _disk_stat(self) -> tuple[int, int] | None:
        """(mtime_ns, size) for the edit target, or None if it can't be
        stat'd (e.g. deleted since edit began -- treated as "changed" by
        disk_changed_since_edit(), which is the right call: overwriting a
        file that vanished out from under the edit still deserves a prompt).
        Two cheap fields rather than hashing file contents: a full-file hash
        on every Save (this file can be edited repeatedly in one session) is
        needless I/O for a check that only needs to catch "something else
        touched this file", not verify byte-for-byte identity -- a
        same-second, same-size rewrite slipping past this is a theoretical
        gap this guard accepts, not a threat it's trying to close.
        """
        if self._abs_file_path is None:
            return None
        try:
            stat = self._abs_file_path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def disk_changed_since_edit(self) -> bool:
        """True if the file no longer matches the fingerprint taken when
        edit mode last read (enter_edit_mode) or wrote (save_edits) it --
        i.e. something else (git pull, another editor, a background
        refresh) rewrote it in between. The widget layer (diff_view_widget)
        consults this before save_edits() and owns the confirmation modal;
        this module stays read-only-of-QMessageBox, matching how the find
        bar (not a QInputDialog) and confirm_and_clear_diff's caller keep
        modals out of the view layer.
        """
        if not self._editing:
            return False
        return self._disk_stat() != self._edit_disk_stat

    def save_edits(self) -> bool:
        if not self._editing or self._abs_file_path is None:
            return False
        text = self._right.toPlainText()
        if self._edit_line_ending == "CRLF":
            text = text.replace("\n", "\r\n")
        try:
            self._abs_file_path.write_bytes(text.encode(self._edit_codec))
        except OSError:
            return False
        self._right.document().setModified(False)
        # Refreshes the fingerprint to the write we just made, so a second
        # Save later in the same edit session compares against this write --
        # not the snapshot taken back when edit mode first opened the file.
        self._edit_disk_stat = self._disk_stat()
        return True

    def _open_find_bar(self) -> None:
        if not self._editing:
            return
        # Anchor the incremental as-you-type search to wherever the cursor
        # sat when the bar was opened, not to whatever the cursor drifts to
        # after each keystroke's match -- otherwise typing "a" then "ab"
        # would search for "ab" starting *after* the "a" match, which can
        # skip straight past an "ab" that begins at the "a" match itself.
        self._find_anchor = self._right.textCursor()
        reopening = self._bar.isVisible()
        self._clear_bar_feedback()
        self._bar.setVisible(True)
        self._bar_input.setFocus()
        if reopening:
            # Ctrl+F on an already-open find bar re-focuses and selects the
            # existing query, per spec, instead of clearing it -- matches
            # how browser find bars behave on a repeat press.
            self._bar_input.selectAll()
        else:
            self._bar_input.clear()

    def _open_goto_bar(self) -> None:
        if not self._editing:
            return
        # The user asked for "a simple popup input dialog" here, and
        # unlike find, a one-shot line-number prompt has no incremental
        # feedback to show while typing that would justify occupying the
        # view with an inline widget -- so this is a modal QInputDialog,
        # not the find bar. Its own min/max args do the out-of-range
        # clamping for free, instead of clamp arithmetic of our own.
        block_count = self._right.document().blockCount()
        current_line = self._right.textCursor().blockNumber() + 1
        line, ok = QInputDialog.getInt(
            self, "Go to Line", "Line number:", current_line, 1, block_count
        )
        if not ok:
            return
        block = self._right.document().findBlockByNumber(line - 1)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        self._right.setTextCursor(cursor)
        self._right.centerCursor()

    def _close_bar(self) -> None:
        self._bar.setVisible(False)
        self._right.setFocus()

    def _reset_bar(self) -> None:
        # Leaving edit mode or navigating to a different file must not
        # leave the find bar dangling over the new buffer, and any find
        # anchor from the old buffer is meaningless.
        self._bar.setVisible(False)
        self._find_anchor = None

    def _on_bar_return(self) -> None:
        self._find_next()

    def _on_bar_shift_return(self) -> None:
        self._find_prev()

    def _on_bar_text_changed(self, _text: str) -> None:
        if not self._bar_input.text():
            self._clear_bar_feedback()
            return
        if self._find_anchor is not None:
            self._right.setTextCursor(self._find_anchor)
        self._do_find(QTextDocument.FindFlag(0))

    def _find_next(self) -> None:
        self._do_find(QTextDocument.FindFlag(0))

    def _find_prev(self) -> None:
        self._do_find(QTextDocument.FindFlag.FindBackward)

    def _do_find(self, flags: QTextDocument.FindFlag) -> None:
        text = self._bar_input.text()
        if not text:
            self._clear_bar_feedback()
            return
        original_cursor = self._right.textCursor()
        found = self._right.find(text, flags)
        if not found:
            # QPlainTextEdit.find() doesn't wrap on its own: move to the
            # document's start (or end, searching backward) and retry once
            # rather than silently reporting no match when the search
            # simply ran off whichever edge.
            wrap_cursor = QTextCursor(original_cursor)
            move = (
                QTextCursor.MoveOperation.End
                if flags & QTextDocument.FindFlag.FindBackward
                else QTextCursor.MoveOperation.Start
            )
            wrap_cursor.movePosition(move)
            self._right.setTextCursor(wrap_cursor)
            found = self._right.find(text, flags)
            if not found:
                # No match anywhere in the document -- restore the
                # original cursor rather than stranding it at the wrap
                # point, per spec ("leaves the cursor put").
                self._right.setTextCursor(original_cursor)
        self._set_bar_feedback(found)

    def _set_bar_feedback(self, found: bool) -> None:
        if found:
            self._clear_bar_feedback()
        else:
            # A reddish input colour is the cheapest, most Qt-idiomatic
            # "no match" signal that doesn't need a second widget; cleared
            # the instant a search succeeds again.
            self._bar_input.setStyleSheet("color: #B91C1C;")

    def _clear_bar_feedback(self) -> None:
        self._bar_input.setStyleSheet("")

    def _rebuild(self) -> None:
        diff = self._diff
        if diff is None:
            return

        # _rebuild always renders diff-row numbering, never edit mode's
        # sequential numbering -- resetting it here (rather than only in
        # exit_edit_mode) means every caller that ends up here (set_diff,
        # exit_edit_mode, fold-expand) is guaranteed correct even if a
        # future caller forgets to reset it explicitly.
        self._left.sequential_line_numbers = False
        self._right.sequential_line_numbers = False

        paired: list[PairedLine] = []
        fold_keys: list[tuple[int, int] | None] = []
        # The diff-row index of each change run's first row, tracked in
        # lockstep with `change_runs()` below: both flush "am I inside a
        # change" tracking on the same two boundaries -- a CONTEXT row (a
        # fold marker's lines are CONTEXT too, just collapsed) and a hunk
        # boundary -- so the two lists always end up the same length and
        # in the same order, letting scroll_to_hunk zip them by index.
        change_run_rows: list[int] = []
        for h_idx, hunk in enumerate(diff.hunks):
            in_run = False
            for seg_idx, segment in enumerate(fold_context(hunk.lines)):
                key = (h_idx, seg_idx)
                if isinstance(segment, FoldedRun) and key not in self._expanded_folds:
                    count = len(segment.lines)
                    marker = f"⋯ {count} unchanged lines — click to expand ⋯"
                    paired.append(PairedLine(marker, None, marker, None))
                    fold_keys.append(key)
                    in_run = False
                    continue

                for p in pair_hunk_lines(segment.lines):
                    is_change_row = p.left_kind is not None or p.right_kind is not None
                    if is_change_row and not in_run:
                        change_run_rows.append(len(paired))
                    in_run = is_change_row
                    paired.append(p)
                    fold_keys.append(None)

        left_lines = [p.left_text if p.left_text is not None else "" for p in paired]
        right_lines = [p.right_text if p.right_text is not None else "" for p in paired]
        self._left.setPlainText("\n".join(left_lines) if left_lines else "(no changes)")
        self._right.setPlainText("\n".join(right_lines) if right_lines else "(no changes)")
        self._left.fold_keys = fold_keys
        self._right.fold_keys = fold_keys
        self._left.line_numbers = [p.left_lineno for p in paired]
        self._right.line_numbers = [p.right_lineno for p in paired]
        self._left._update_gutter_width()
        self._right._update_gutter_width()
        self._left._gutter.update()
        self._right._gutter.update()
        if self._file_path is not None:
            self._left.highlighter.set_filename(self._file_path)
            self._right.highlighter.set_filename(self._file_path)

        left_ranges: list[list[tuple[int, int]]] = []
        right_ranges: list[list[tuple[int, int]]] = []
        for p in paired:
            if p.left_kind is DiffLineKind.REMOVED and p.right_kind is DiffLineKind.ADDED:
                old_ranges, new_ranges = intraline_ranges(p.left_text or "", p.right_text or "")
                left_ranges.append(old_ranges)
                right_ranges.append(new_ranges)
            else:
                left_ranges.append([])
                right_ranges.append([])

        self._highlight(self._left, [p.left_kind for p in paired], left_ranges)
        self._highlight(self._right, [p.right_kind for p in paired], right_ranges)
        self._change_runs = change_runs(diff)
        self._change_run_rows = change_run_rows

    def hunk_count(self) -> int:
        return len(self._change_runs)

    def scroll_to_hunk(self, index: int) -> None:
        if not 0 <= index < len(self._change_runs):
            return
        if self._editing:
            self._scroll_to_change_run_in_edit_mode(self._change_runs[index])
        else:
            self._scroll_to_change_run_in_diff_mode(index)

    def _scroll_to_change_run_in_diff_mode(self, index: int) -> None:
        if not 0 <= index < len(self._change_run_rows):
            return
        block = self._left.document().findBlockByNumber(self._change_run_rows[index])
        cursor = QTextCursor(block)
        self._left.setTextCursor(cursor)
        self._left.centerCursor()

    def _scroll_to_change_run_in_edit_mode(self, run: ChangeRun) -> None:
        # Both panes hold their own whole file with real line numbers in
        # edit mode -- scroll each to its own real line rather than a
        # shared diff-row. Sync-scroll mirrors raw scrollbar values between
        # the panes (_sync_vertical_from_left/_sync_vertical_from_right),
        # so without suppressing it via the existing `_syncing` guard,
        # scrolling one pane here would immediately overwrite the other's
        # position with its own (unrelated) scrollbar value.
        self._syncing = True
        try:
            self._scroll_pane_to_line(self._left, run.old_lineno)
            self._scroll_pane_to_line(self._right, run.new_lineno)
        finally:
            self._syncing = False
        # Focus stays in the editable right pane so the user can type at
        # the change immediately; the left pane is scrolled without being
        # given focus.
        self._right.setFocus()

    @staticmethod
    def _scroll_pane_to_line(pane: "_DiffPane", lineno: int) -> None:
        block_number = max(0, min(lineno - 1, max(pane.document().blockCount() - 1, 0)))
        block = pane.document().findBlockByNumber(block_number)
        cursor = QTextCursor(block)
        pane.setTextCursor(cursor)
        pane.centerCursor()

    def clear_diff(self) -> None:
        self._diff = None
        self._file_path = None
        self._abs_file_path = None
        self._expanded_folds = set()
        self._change_runs = []
        self._change_run_rows = []
        self._editing = False
        self._edit_disk_stat = None
        self._right.setReadOnly(True)
        self._left.fold_keys = []
        self._right.fold_keys = []
        self._left.line_numbers = []
        self._right.line_numbers = []
        self._left.sequential_line_numbers = False
        self._right.sequential_line_numbers = False
        self._left.setPlainText("")
        self._right.setPlainText("")
        self._reset_bar()

    def _highlight(
        self,
        pane: QPlainTextEdit,
        kinds: list[DiffLineKind | None],
        intraline: list[list[tuple[int, int]]],
    ) -> None:
        selections = []
        block = pane.document().firstBlock()
        for kind, ranges in zip(kinds, intraline):
            bg = _LINE_BG.get(kind)
            if bg is not None and block.isValid():
                selection = QTextEdit.ExtraSelection()
                selection.format.setBackground(bg)
                selection.format.setForeground(_LINE_FG[kind])
                selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                cursor = pane.textCursor()
                cursor.setPosition(block.position())
                selection.cursor = cursor
                selections.append(selection)

                for start, end in ranges:
                    sub_selection = QTextEdit.ExtraSelection()
                    sub_selection.format.setBackground(_INTRALINE_BG[kind])
                    sub_selection.format.setForeground(_LINE_FG[kind])
                    sub_cursor = pane.textCursor()
                    sub_cursor.setPosition(block.position() + start)
                    sub_cursor.setPosition(block.position() + end, QTextCursor.MoveMode.KeepAnchor)
                    sub_selection.cursor = sub_cursor
                    selections.append(sub_selection)
            block = block.next()
        pane.setExtraSelections(selections)
