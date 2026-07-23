from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from local_changes_viewer.core.domain.diff import DiffLineKind, DiffResult
from local_changes_viewer.core.services.context_folding import FoldedRun, fold_context
from local_changes_viewer.core.services.diff_pairing import pair_substitution_indices
from local_changes_viewer.core.services.intraline_diff import intraline_ranges
from local_changes_viewer.gui.diff_view.syntax_highlighter import PygmentsHighlighter

_GUTTER_BG = {
    DiffLineKind.ADDED: QColor("#DCFCE7"),
    DiffLineKind.REMOVED: QColor("#FEE2E2"),
}
_INTRALINE_BG = {
    DiffLineKind.ADDED: QColor("#86EFAC"),
    DiffLineKind.REMOVED: QColor("#FCA5A5"),
}


@dataclass
class _LineMeta:
    old_lineno: int | None
    new_lineno: int | None
    kind: DiffLineKind | None  # None for hunk-header/fold-marker rows
    intraline_ranges: list[tuple[int, int]] | None = None
    fold_key: tuple[int, int] | None = None


class _GutterWidget(QWidget):
    def __init__(self, editor: "UnifiedView") -> None:
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self._editor.gutter_width(), 0)

    def paintEvent(self, event) -> None:
        self._editor.paint_gutter(event)


class UnifiedView(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFont("Menlo", 12))
        self._line_meta: list[_LineMeta] = []
        self._diff: DiffResult | None = None
        self._file_path: str | None = None
        self._expanded_folds: set[tuple[int, int]] = set()
        self._gutter = _GutterWidget(self)
        self._highlighter = PygmentsHighlighter(self.document(), prefix_len=1)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter_area)
        self._update_gutter_width()

    def set_diff(self, diff: DiffResult, file_path: str | None = None) -> None:
        self._diff = diff
        self._file_path = file_path
        self._expanded_folds = set()
        self._rebuild()

    def clear_diff(self) -> None:
        self._diff = None
        self._file_path = None
        self._expanded_folds = set()
        self._line_meta = []
        self.setPlainText("")
        self._update_gutter_width()
        self.setExtraSelections([])

    def _rebuild(self) -> None:
        diff = self._diff
        if diff is None:
            return

        lines: list[str] = []
        meta: list[_LineMeta] = []
        for h_idx, hunk in enumerate(diff.hunks):
            lines.append(
                f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@"
            )
            meta.append(_LineMeta(None, None, None))

            intraline_by_index: dict[int, list[tuple[int, int]]] = {}
            for removed_idx, added_idx in pair_substitution_indices(hunk.lines):
                old_ranges, new_ranges = intraline_ranges(
                    hunk.lines[removed_idx].text, hunk.lines[added_idx].text
                )
                intraline_by_index[removed_idx] = old_ranges
                intraline_by_index[added_idx] = new_ranges

            orig_idx = 0
            for seg_idx, segment in enumerate(fold_context(hunk.lines)):
                key = (h_idx, seg_idx)
                if isinstance(segment, FoldedRun) and key not in self._expanded_folds:
                    count = len(segment.lines)
                    lines.append(f"⋯ {count} unchanged lines — click to expand ⋯")
                    meta.append(_LineMeta(None, None, None, fold_key=key))
                    orig_idx += count
                    continue

                for line in segment.lines:
                    prefix = {"ADDED": "+", "REMOVED": "-", "CONTEXT": " "}[line.kind.name]
                    lines.append(f"{prefix}{line.text}")
                    meta.append(
                        _LineMeta(
                            line.old_lineno,
                            line.new_lineno,
                            line.kind,
                            intraline_ranges=intraline_by_index.get(orig_idx),
                        )
                    )
                    orig_idx += 1

        self._line_meta = meta
        self.setPlainText("\n".join(lines) if lines else "(no changes)")
        if self._file_path is not None:
            self._highlighter.set_filename(self._file_path)
        self._update_gutter_width()
        self._gutter.update()
        self._update_intraline_selections()

    def mousePressEvent(self, event) -> None:
        cursor = self.cursorForPosition(event.pos())
        block_number = cursor.blockNumber()
        if block_number < len(self._line_meta):
            fold_key = self._line_meta[block_number].fold_key
            if fold_key is not None:
                self._expanded_folds.add(fold_key)
                self._rebuild()
                return
        super().mousePressEvent(event)

    def _update_intraline_selections(self) -> None:
        selections = []
        block = self.document().firstBlock()
        block_number = 0
        while block.isValid():
            if block_number < len(self._line_meta):
                meta = self._line_meta[block_number]
                if meta.kind is not None and meta.intraline_ranges:
                    for start, end in meta.intraline_ranges:
                        selection = QTextEdit.ExtraSelection()
                        selection.format.setBackground(_INTRALINE_BG[meta.kind])
                        cursor = QTextCursor(block)
                        # +1 to skip the diff-marker prefix character.
                        cursor.setPosition(block.position() + 1 + start)
                        cursor.setPosition(
                            block.position() + 1 + end, QTextCursor.MoveMode.KeepAnchor
                        )
                        selection.cursor = cursor
                        selections.append(selection)
            block = block.next()
            block_number += 1
        self.setExtraSelections(selections)

    def gutter_width(self) -> int:
        digits = max((len(str(m.old_lineno or 0)) for m in self._line_meta), default=1)
        digits = max(
            digits, max((len(str(m.new_lineno or 0)) for m in self._line_meta), default=1)
        )
        digits = max(digits, 2)
        metrics = QFontMetrics(self.font())
        return metrics.horizontalAdvance("9") * (digits * 2 + 4) + 10

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
        half_width = (self.gutter_width() - 10) // 2

        while block.isValid() and top <= event.rect().bottom():
            if (
                block.isVisible()
                and bottom >= event.rect().top()
                and block_number < len(self._line_meta)
            ):
                meta = self._line_meta[block_number]
                if meta.kind is not None:
                    bg = _GUTTER_BG.get(meta.kind)
                    if bg is not None:
                        painter.fillRect(0, top, self._gutter.width(), bottom - top, bg)
                    old_text = str(meta.old_lineno) if meta.old_lineno else ""
                    new_text = str(meta.new_lineno) if meta.new_lineno else ""
                    painter.setPen(QColor("#6B7280"))
                    painter.drawText(
                        0, top, half_width, metrics.height(), Qt.AlignmentFlag.AlignRight, old_text
                    )
                    painter.drawText(
                        half_width + 5,
                        top,
                        half_width,
                        metrics.height(),
                        Qt.AlignmentFlag.AlignRight,
                        new_text,
                    )

            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            block_number += 1
