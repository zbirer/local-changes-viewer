from PySide6.QtGui import QColor, QFont, QTextCursor, QTextFormat
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QSplitter, QTextEdit, QWidget

from local_changes_viewer.core.domain.diff import DiffLineKind, DiffResult
from local_changes_viewer.core.services.diff_pairing import PairedLine, pair_hunk_lines
from local_changes_viewer.core.services.intraline_diff import intraline_ranges
from local_changes_viewer.gui.diff_view.syntax_highlighter import PygmentsHighlighter

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


class _DiffPane(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont("Menlo", 12))
        self.highlighter = PygmentsHighlighter(self.document())


class SideBySideView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._left = _DiffPane()
        self._right = _DiffPane()
        self._syncing = False
        self._left.verticalScrollBar().valueChanged.connect(self._sync_from_left)
        self._right.verticalScrollBar().valueChanged.connect(self._sync_from_right)

        splitter = QSplitter()
        splitter.addWidget(self._left)
        splitter.addWidget(self._right)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    def _sync_from_left(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self._right.verticalScrollBar().setValue(value)
        self._syncing = False

    def _sync_from_right(self, value: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        self._left.verticalScrollBar().setValue(value)
        self._syncing = False

    def set_diff(self, diff: DiffResult, file_path: str | None = None) -> None:
        paired: list[PairedLine] = []
        for hunk in diff.hunks:
            paired.extend(pair_hunk_lines(hunk.lines))

        left_lines = [p.left_text if p.left_text is not None else "" for p in paired]
        right_lines = [p.right_text if p.right_text is not None else "" for p in paired]
        self._left.setPlainText("\n".join(left_lines) if left_lines else "(no changes)")
        self._right.setPlainText("\n".join(right_lines) if right_lines else "(no changes)")
        if file_path is not None:
            self._left.highlighter.set_filename(file_path)
            self._right.highlighter.set_filename(file_path)

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

    def clear_diff(self) -> None:
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
