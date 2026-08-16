from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QTextCursor
from PySide6.QtWidgets import QMenu, QPlainTextEdit, QTextEdit, QWidget

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
    is_hunk_header: bool = False


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
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._line_meta: list[_LineMeta] = []
        self._change_run_rows: list[int] = []
        self._diff: DiffResult | None = None
        self._file_path: str | None = None
        self._abs_file_path: Path | None = None
        self._expanded_folds: set[tuple[int, int]] = set()
        self._line_numbers_visible = True
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

    def set_file_target(self, abs_file_path: Path | None) -> None:
        """The absolute on-disk path "Copy Location" reports, mirroring
        side_by_side_view.py's set_file_target -- same value (or None where
        main_window's _edit_target found no safe edit target: a deleted
        file, a folder, or an already-committed-but-unpushed diff), just
        threaded to this view too so unified mode gets the same feature.
        """
        self._abs_file_path = abs_file_path

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

    def clear_diff(self) -> None:
        self._diff = None
        self._file_path = None
        self._abs_file_path = None
        self._expanded_folds = set()
        self._line_meta = []
        self._change_run_rows = []
        self.setPlainText("")
        self._update_gutter_width()
        self.setExtraSelections([])

    def _rebuild(self) -> None:
        diff = self._diff
        if diff is None:
            return

        lines: list[str] = []
        meta: list[_LineMeta] = []
        # The block index of each change run's first row -- a change run
        # is a maximal contiguous group of ADDED/REMOVED rows, never split
        # across a hunk boundary or a CONTEXT row (a fold marker's rows are
        # CONTEXT too, just collapsed). This is the navigation unit for
        # "Prev change"/"Next change": `compute_diff`'s `--unified=100000`
        # usually collapses a whole file into a single git hunk, so
        # counting/jumping by hunk header (the old behavior) only ever
        # found one target -- see diff_pairing.change_runs for the
        # equivalent, independently-tested logic on the side-by-side view.
        change_run_rows: list[int] = []
        for h_idx, hunk in enumerate(diff.hunks):
            lines.append(
                f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@"
            )
            meta.append(_LineMeta(None, None, None, is_hunk_header=True))
            in_run = False

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
                    in_run = False
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
                    is_change_row = line.kind is not DiffLineKind.CONTEXT
                    if is_change_row and not in_run:
                        change_run_rows.append(len(meta) - 1)
                    in_run = is_change_row
                    orig_idx += 1

        self._line_meta = meta
        self._change_run_rows = change_run_rows
        self.setPlainText("\n".join(lines) if lines else "(no changes)")
        if self._file_path is not None:
            self._highlighter.set_filename(self._file_path)
        self._update_gutter_width()
        self._gutter.update()
        self._update_intraline_selections()

    def hunk_count(self) -> int:
        return len(self._change_run_rows)

    def scroll_to_hunk(self, index: int) -> None:
        if not 0 <= index < len(self._change_run_rows):
            return
        block = self.document().findBlockByNumber(self._change_run_rows[index])
        cursor = QTextCursor(block)
        self.setTextCursor(cursor)
        self.centerCursor()

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

    def _location_at(self, pos: QPoint) -> tuple[Path, int] | None:
        """Maps a right-click position to the `(abs_path, line)` pair
        "Copy Location" should put on the clipboard, or None where the row
        under the click has no real file line to report at all. The line
        always comes from the clicked DiffLine's own old_lineno/new_lineno
        -- never the visual row index -- because a hunk header or a
        collapsed fold marker sits at a row with no corresponding line in
        either file, and a REMOVED line has no new_lineno (it never made
        it into the new file), so falling back to new_lineno there would
        silently report an unrelated line instead of disabling the action.
        A CONTEXT line is unambiguous (same line either side), so it and
        ADDED both read new_lineno.
        """
        if self._abs_file_path is None:
            return None
        block_number = self.cursorForPosition(pos).blockNumber()
        if block_number >= len(self._line_meta):
            return None
        meta = self._line_meta[block_number]
        if meta.kind is None:
            return None
        lineno = meta.old_lineno if meta.kind is DiffLineKind.REMOVED else meta.new_lineno
        if lineno is None:
            return None
        return self._abs_file_path, lineno

    def _build_context_menu(self, pos: QPoint) -> QMenu:
        """Split out from contextMenuEvent so tests can build the menu and
        trigger its "Copy Location" action without calling QMenu.exec() --
        under the offscreen platform used in tests, exec() opens a real
        native modal loop that never gets a click to close it and hangs
        forever (see test_main_window.py's _capture_menu for the same
        constraint on a different menu).
        """
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        location = self._location_at(pos)
        copy_location_action = menu.addAction("Copy Location")
        copy_location_action.setEnabled(location is not None)
        if location is not None:
            abs_path, lineno = location
            copy_location_action.triggered.connect(
                lambda: QGuiApplication.clipboard().setText(f"{abs_path}:{lineno}")
            )
        return menu

    def contextMenuEvent(self, event) -> None:
        menu = self._build_context_menu(event.pos())
        menu.exec(event.globalPos())

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
                        selection.format.setForeground(QColor("#000000"))
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
        if not self._line_numbers_visible:
            return 0
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
