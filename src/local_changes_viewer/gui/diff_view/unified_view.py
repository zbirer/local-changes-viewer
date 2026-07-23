from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from local_changes_viewer.core.domain.diff import DiffLineKind, DiffResult

_GUTTER_BG = {
    DiffLineKind.ADDED: QColor("#DCFCE7"),
    DiffLineKind.REMOVED: QColor("#FEE2E2"),
}


@dataclass
class _LineMeta:
    old_lineno: int | None
    new_lineno: int | None
    kind: DiffLineKind | None  # None for hunk-header rows


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
        self._gutter = _GutterWidget(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter_area)
        self._update_gutter_width()

    def set_diff(self, diff: DiffResult) -> None:
        lines: list[str] = []
        meta: list[_LineMeta] = []
        for hunk in diff.hunks:
            lines.append(
                f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@"
            )
            meta.append(_LineMeta(None, None, None))
            for line in hunk.lines:
                prefix = {"ADDED": "+", "REMOVED": "-", "CONTEXT": " "}[line.kind.name]
                lines.append(f"{prefix}{line.text}")
                meta.append(_LineMeta(line.old_lineno, line.new_lineno, line.kind))
        self._line_meta = meta
        self.setPlainText("\n".join(lines) if lines else "(no changes)")
        self._update_gutter_width()
        self._gutter.update()

    def clear_diff(self) -> None:
        self._line_meta = []
        self.setPlainText("")
        self._update_gutter_width()

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
