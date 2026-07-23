from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.gui.diff_view.side_by_side_view import SideBySideView
from local_changes_viewer.gui.diff_view.unified_view import UnifiedView


class DiffViewWidget(QWidget):
    refresh_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._current_hunk_index = -1
        self._unified = UnifiedView()
        self._side_by_side = SideBySideView()

        self._stack = QStackedWidget()
        self._stack.addWidget(self._unified)
        self._stack.addWidget(self._side_by_side)

        self._toggle_button = QPushButton("Side-by-side")
        self._toggle_button.setCheckable(True)
        self._toggle_button.toggled.connect(self._on_toggled)

        self._prev_button = QPushButton("Prev change")
        self._prev_button.clicked.connect(self._on_prev)
        self._next_button = QPushButton("Next change")
        self._next_button.clicked.connect(self._on_next)

        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.clicked.connect(self.refresh_requested.emit)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.addWidget(self._toggle_button)
        toolbar_layout.addWidget(self._prev_button)
        toolbar_layout.addWidget(self._next_button)
        toolbar_layout.addWidget(self._refresh_button)
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

    def set_diff(self, diff: DiffResult, file_path: str | None = None) -> None:
        self._current_hunk_index = -1
        self._unified.set_diff(diff, file_path)
        self._side_by_side.set_diff(diff, file_path)
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
        self._header_label.setText("")
