from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QTextFormat
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit, QSplitter, QTextEdit, QWidget

from local_changes_viewer.core.domain.diff import DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.gui.diff_view.syntax_highlighter import PygmentsHighlighter

_LINE_BG = {
    DiffLineKind.ADDED: QColor("#DCFCE7"),
    DiffLineKind.REMOVED: QColor("#FEE2E2"),
}
_LINE_FG = {
    DiffLineKind.ADDED: QColor("#065F46"),
    DiffLineKind.REMOVED: QColor("#991B1B"),
}


@dataclass
class _PairedLine:
    left_text: str | None
    left_kind: DiffLineKind | None
    right_text: str | None
    right_kind: DiffLineKind | None


def pair_hunk_lines(lines: list[DiffLine]) -> list[_PairedLine]:
    paired: list[_PairedLine] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.kind is DiffLineKind.CONTEXT:
            paired.append(_PairedLine(line.text, None, line.text, None))
            i += 1
            continue

        removed: list[DiffLine] = []
        while i < len(lines) and lines[i].kind is DiffLineKind.REMOVED:
            removed.append(lines[i])
            i += 1
        added: list[DiffLine] = []
        while i < len(lines) and lines[i].kind is DiffLineKind.ADDED:
            added.append(lines[i])
            i += 1

        for row in range(max(len(removed), len(added))):
            left = removed[row] if row < len(removed) else None
            right = added[row] if row < len(added) else None
            paired.append(
                _PairedLine(
                    left.text if left is not None else None,
                    DiffLineKind.REMOVED if left is not None else None,
                    right.text if right is not None else None,
                    DiffLineKind.ADDED if right is not None else None,
                )
            )
    return paired


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
        paired: list[_PairedLine] = []
        for hunk in diff.hunks:
            paired.extend(pair_hunk_lines(hunk.lines))

        left_lines = [p.left_text if p.left_text is not None else "" for p in paired]
        right_lines = [p.right_text if p.right_text is not None else "" for p in paired]
        self._left.setPlainText("\n".join(left_lines) if left_lines else "(no changes)")
        self._right.setPlainText("\n".join(right_lines) if right_lines else "(no changes)")
        if file_path is not None:
            self._left.highlighter.set_filename(file_path)
            self._right.highlighter.set_filename(file_path)
        self._highlight(self._left, [p.left_kind for p in paired])
        self._highlight(self._right, [p.right_kind for p in paired])

    def clear_diff(self) -> None:
        self._left.setPlainText("")
        self._right.setPlainText("")

    def _highlight(self, pane: QPlainTextEdit, kinds: list[DiffLineKind | None]) -> None:
        selections = []
        block = pane.document().firstBlock()
        for kind in kinds:
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
            block = block.next()
        pane.setExtraSelections(selections)
