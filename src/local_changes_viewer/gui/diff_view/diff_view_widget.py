from PySide6.QtWidgets import QHBoxLayout, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.gui.diff_view.side_by_side_view import SideBySideView
from local_changes_viewer.gui.diff_view.unified_view import UnifiedView


class DiffViewWidget(QWidget):
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

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.addWidget(self._toggle_button)
        toolbar_layout.addWidget(self._prev_button)
        toolbar_layout.addWidget(self._next_button)
        toolbar_layout.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar_layout)
        layout.addWidget(self._stack)

    def _active_view(self) -> UnifiedView | SideBySideView:
        return self._side_by_side if self._toggle_button.isChecked() else self._unified

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

    def clear_diff(self) -> None:
        self._current_hunk_index = -1
        self._unified.clear_diff()
        self._side_by_side.clear_diff()
