from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.gui.diff_view.side_by_side_view import SideBySideView
from local_changes_viewer.gui.diff_view.unified_view import UnifiedView


class DiffViewWidget(QWidget):
    refresh_requested = Signal()
    time_filter_minutes_changed = Signal(int)
    file_saved = Signal(str)
    pull_requests_requested = Signal()

    _MAX_TIME_FILTER_MINUTES = 180

    _MIN_FONT_POINT_SIZE = 6
    _MAX_FONT_POINT_SIZE = 36

    def __init__(self) -> None:
        super().__init__()
        # Overrides the toolbar's cumulative minimum width so the splitter divider
        # can be dragged nearly all the way to the right instead of stopping where
        # the toolbar buttons would otherwise be clipped.
        self.setMinimumWidth(1)
        self._current_hunk_index = -1
        self._font_point_size = 12
        self._unified = UnifiedView()
        self._side_by_side = SideBySideView()

        self._stack = QStackedWidget()
        self._stack.addWidget(self._unified)
        self._stack.addWidget(self._side_by_side)

        self._pull_requests_button = QPushButton("PRs")
        self._pull_requests_button.clicked.connect(self.pull_requests_requested.emit)

        self._toggle_button = QPushButton("Side-by-side")
        self._toggle_button.setCheckable(True)
        self._toggle_button.toggled.connect(self._on_toggled)

        self._prev_button = QPushButton("Prev change")
        self._prev_button.clicked.connect(self._on_prev)
        self._next_button = QPushButton("Next change")
        self._next_button.clicked.connect(self._on_next)

        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.clicked.connect(self.refresh_requested.emit)

        self._edit_button = QPushButton("Edit")
        self._edit_button.setCheckable(True)
        self._edit_button.setEnabled(False)
        self._edit_button.toggled.connect(self._on_edit_toggled)

        self._save_button = QPushButton("Save")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self._on_save_clicked)

        self._line_numbers_button = QPushButton("Line Numbers")
        self._line_numbers_button.setCheckable(True)
        self._line_numbers_button.setChecked(False)
        self._line_numbers_button.toggled.connect(self._on_line_numbers_toggled)
        self._on_line_numbers_toggled(False)

        self._time_filter_slider = QSlider(Qt.Orientation.Horizontal)
        self._time_filter_slider.setRange(0, self._MAX_TIME_FILTER_MINUTES)
        self._time_filter_slider.setValue(0)
        self._time_filter_slider.setFixedWidth(150)
        self._time_filter_slider.valueChanged.connect(self._on_time_filter_changed)

        self._time_filter_label = QLabel("All changes")

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.addWidget(self._pull_requests_button)
        toolbar_layout.addWidget(self._toggle_button)
        toolbar_layout.addWidget(self._prev_button)
        toolbar_layout.addWidget(self._next_button)
        toolbar_layout.addWidget(self._refresh_button)
        toolbar_layout.addWidget(self._edit_button)
        toolbar_layout.addWidget(self._save_button)
        toolbar_layout.addWidget(self._line_numbers_button)
        toolbar_layout.addWidget(self._time_filter_slider)
        toolbar_layout.addWidget(self._time_filter_label)
        toolbar_layout.addStretch()

        self._header_label = QLabel("")
        self._header_label.setStyleSheet("color: #6B7280;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar_layout)
        layout.addWidget(self._header_label)
        layout.addWidget(self._stack)

    def _active_view(self) -> UnifiedView | SideBySideView:
        return self._side_by_side if self._toggle_button.isChecked() else self._unified

    def is_side_by_side(self) -> bool:
        return self._toggle_button.isChecked()

    def set_side_by_side(self, enabled: bool) -> None:
        self._toggle_button.setChecked(enabled)

    def set_sync_scroll(self, enabled: bool) -> None:
        self._side_by_side.set_sync_scroll(enabled)

    def set_pull_requests_button_enabled(self, enabled: bool) -> None:
        self._pull_requests_button.setEnabled(enabled)

    def increase_font_size(self) -> None:
        self._set_font_point_size(self._font_point_size + 1)

    def decrease_font_size(self) -> None:
        self._set_font_point_size(self._font_point_size - 1)

    def set_time_filter_minutes(self, minutes: int) -> None:
        self._time_filter_slider.setValue(minutes)

    def _set_font_point_size(self, size: int) -> None:
        size = max(self._MIN_FONT_POINT_SIZE, min(self._MAX_FONT_POINT_SIZE, size))
        self._font_point_size = size
        self._unified.set_font_point_size(size)
        self._side_by_side.set_font_point_size(size)

    def _on_time_filter_changed(self, minutes: int) -> None:
        self._time_filter_label.setText("All changes" if minutes == 0 else f"Last {minutes} min")
        self.time_filter_minutes_changed.emit(minutes)

    def _on_toggled(self, checked: bool) -> None:
        self._stack.setCurrentIndex(1 if checked else 0)
        self._toggle_button.setText("Unified" if checked else "Side-by-side")

    def _on_prev(self) -> None:
        view = self._active_view()
        if view.hunk_count() == 0:
            return
        self._current_hunk_index = max(self._current_hunk_index - 1, 0)
        view.scroll_to_hunk(self._current_hunk_index)

    def _on_next(self) -> None:
        view = self._active_view()
        count = view.hunk_count()
        if count == 0:
            return
        self._current_hunk_index = min(self._current_hunk_index + 1, count - 1)
        view.scroll_to_hunk(self._current_hunk_index)

    def set_diff(
        self,
        diff: DiffResult,
        file_path: str | None = None,
        abs_file_path: Path | None = None,
    ) -> None:
        self._current_hunk_index = -1
        self._unified.set_diff(diff, file_path)
        self._side_by_side.set_diff(diff, file_path)
        self._side_by_side.set_file_target(abs_file_path)
        self._edit_button.setChecked(False)
        self._edit_button.setEnabled(abs_file_path is not None)
        self._save_button.setEnabled(False)
        if diff.old_blob_id and diff.new_blob_id:
            self._header_label.setText(
                f"index {diff.old_blob_id}..{diff.new_blob_id}  ({diff.old_ref} → {diff.new_ref})"
            )
        else:
            self._header_label.setText(f"{diff.old_ref} → {diff.new_ref}")

    def clear_diff(self) -> None:
        self._current_hunk_index = -1
        self._unified.clear_diff()
        self._side_by_side.clear_diff()
        self._edit_button.setChecked(False)
        self._edit_button.setEnabled(False)
        self._save_button.setEnabled(False)
        self._header_label.setText("")

    def has_unsaved_edits(self) -> bool:
        return self._side_by_side.has_unsaved_edits()

    def discard_edits_if_any(self) -> bool:
        """Silently exits edit mode, discarding unsaved edits. Returns True if anything was discarded."""
        had_unsaved = self._side_by_side.has_unsaved_edits()
        if self._side_by_side.is_editing():
            self._side_by_side.exit_edit_mode()
            self._edit_button.setChecked(False)
            self._save_button.setEnabled(False)
        return had_unsaved

    def _on_edit_toggled(self, checked: bool) -> None:
        if checked:
            self.set_side_by_side(True)
            if not self._side_by_side.enter_edit_mode():
                self._edit_button.setChecked(False)
                return
            self._save_button.setEnabled(True)
            return

        if self._side_by_side.has_unsaved_edits():
            reply = QMessageBox.question(
                self,
                "Discard edits?",
                "You have unsaved edits to this file. Discard them?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                self._edit_button.setChecked(True)
                return
        self._side_by_side.exit_edit_mode()
        self._save_button.setEnabled(False)

    def _on_save_clicked(self) -> None:
        if self._side_by_side.save_edits():
            self.file_saved.emit(str(self._side_by_side.file_target()))

    def _on_line_numbers_toggled(self, checked: bool) -> None:
        self._unified.set_line_numbers_visible(checked)
        self._side_by_side.set_line_numbers_visible(checked)
