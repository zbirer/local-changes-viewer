from PySide6.QtWidgets import QPushButton, QStackedWidget, QVBoxLayout, QWidget

from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.gui.diff_view.side_by_side_view import SideBySideView
from local_changes_viewer.gui.diff_view.unified_view import UnifiedView


class DiffViewWidget(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._unified = UnifiedView()
        self._side_by_side = SideBySideView()

        self._stack = QStackedWidget()
        self._stack.addWidget(self._unified)
        self._stack.addWidget(self._side_by_side)

        self._toggle_button = QPushButton("Side-by-side")
        self._toggle_button.setCheckable(True)
        self._toggle_button.toggled.connect(self._on_toggled)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._toggle_button)
        layout.addWidget(self._stack)

    def _on_toggled(self, checked: bool) -> None:
        self._stack.setCurrentIndex(1 if checked else 0)
        self._toggle_button.setText("Unified" if checked else "Side-by-side")

    def set_diff(self, diff: DiffResult, file_path: str | None = None) -> None:
        self._unified.set_diff(diff, file_path)
        self._side_by_side.set_diff(diff, file_path)

    def clear_diff(self) -> None:
        self._unified.clear_diff()
        self._side_by_side.clear_diff()
