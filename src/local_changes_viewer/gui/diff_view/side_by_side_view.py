from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QTextCursor, QTextFormat
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QSplitter, QTextEdit, QWidget

from local_changes_viewer.core.domain.diff import DiffLineKind, DiffResult
from local_changes_viewer.core.services.context_folding import FoldedRun, fold_context
from local_changes_viewer.core.services.diff_pairing import PairedLine, pair_hunk_lines
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
            if (
                block.isVisible()
                and bottom >= event.rect().top()
                and block_number < len(self.line_numbers)
            ):
                lineno = self.line_numbers[block_number]
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


class SideBySideView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._diff: DiffResult | None = None
        self._file_path: str | None = None
        self._abs_file_path: Path | None = None
        self._expanded_folds: set[tuple[int, int]] = set()
        self._hunk_start_rows: list[int] = []
        self._editing = False
        self._edit_codec = "utf-8"
        self._edit_line_ending = "LF"
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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

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
        self._right.fold_keys = []
        self._right.setExtraSelections([])
        self._right.setPlainText(text)
        self._right.document().setModified(False)
        self._right.setReadOnly(False)
        return True

    def exit_edit_mode(self) -> None:
        self._editing = False
        self._right.setReadOnly(True)
        self._rebuild()

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
        return True

    def _rebuild(self) -> None:
        diff = self._diff
        if diff is None:
            return

        paired: list[PairedLine] = []
        fold_keys: list[tuple[int, int] | None] = []
        hunk_start_rows: list[int] = []
        for h_idx, hunk in enumerate(diff.hunks):
            hunk_start_rows.append(len(paired))
            for seg_idx, segment in enumerate(fold_context(hunk.lines)):
                key = (h_idx, seg_idx)
                if isinstance(segment, FoldedRun) and key not in self._expanded_folds:
                    count = len(segment.lines)
                    marker = f"⋯ {count} unchanged lines — click to expand ⋯"
                    paired.append(PairedLine(marker, None, marker, None))
                    fold_keys.append(key)
                    continue

                for p in pair_hunk_lines(segment.lines):
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
        self._hunk_start_rows = hunk_start_rows

    def hunk_count(self) -> int:
        return len(self._hunk_start_rows)

    def scroll_to_hunk(self, index: int) -> None:
        if not 0 <= index < len(self._hunk_start_rows):
            return
        block = self._left.document().findBlockByNumber(self._hunk_start_rows[index])
        cursor = QTextCursor(block)
        self._left.setTextCursor(cursor)
        self._left.centerCursor()

    def clear_diff(self) -> None:
        self._diff = None
        self._file_path = None
        self._abs_file_path = None
        self._expanded_folds = set()
        self._hunk_start_rows = []
        self._editing = False
        self._right.setReadOnly(True)
        self._left.fold_keys = []
        self._right.fold_keys = []
        self._left.line_numbers = []
        self._right.line_numbers = []
        self._left.setPlainText("")
        self._right.setPlainText("")

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
