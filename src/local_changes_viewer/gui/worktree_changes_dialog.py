from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.gui.diff_view.side_by_side_view import SideBySideView
from local_changes_viewer.gui.diff_view.unified_view import UnifiedView

_FILE_CHANGE_ROLE = Qt.ItemDataRole.UserRole


class WorktreeChangesDialog(QDialog):
    def __init__(
        self, worktree_path: Path, adapter_factory=GitRepoAdapter, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._worktree_path = worktree_path
        self._adapter = adapter_factory(worktree_path)
        self.setWindowTitle(f"Changes — {worktree_path.name}")
        parent_width = parent.width() if parent is not None else 1100
        self.resize(int(parent_width * 0.8), 600)

        self._file_list = QListWidget()
        self._file_list.currentItemChanged.connect(self._on_file_selected)

        self._unified_diff_view = UnifiedView()
        self._side_by_side_diff_view = SideBySideView()
        self._diff_stack = QStackedWidget()
        self._diff_stack.addWidget(self._unified_diff_view)
        self._diff_stack.addWidget(self._side_by_side_diff_view)

        self._diff_toggle_button = QPushButton("Side-by-side")
        self._diff_toggle_button.setToolTip("Toggle side-by-side / unified diff view")
        self._diff_toggle_button.setCheckable(True)
        self._diff_toggle_button.toggled.connect(self._on_diff_toggled)

        diff_header = QWidget()
        diff_header_layout = QHBoxLayout(diff_header)
        diff_header_layout.setContentsMargins(0, 0, 0, 0)
        diff_header_layout.addWidget(self._diff_toggle_button)
        diff_header_layout.addStretch()

        file_panel = self._labeled_panel("Files Changed", self._file_list)
        diff_panel = self._labeled_panel("Diff", self._diff_stack, extra_header=diff_header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(file_panel)
        splitter.addWidget(diff_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self._load_changes()

    @staticmethod
    def _labeled_panel(title: str, widget: QWidget, extra_header: QWidget | None = None) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        label.setStyleSheet("color: #6B7280;")
        layout.addWidget(label)
        if extra_header is not None:
            layout.addWidget(extra_header)
        layout.addWidget(widget)
        return panel

    def _load_changes(self) -> None:
        try:
            changes = self._adapter.list_changes(include_unpushed_commits=True)
        except Exception as exc:
            QMessageBox.warning(self, "Show Changes failed", f"Failed to read changes: {exc}")
            changes = []
        changes = [c for c in changes if c.change_type != ChangeType.IGNORED]

        for change in changes:
            status = "Committed" if change.is_unpushed_commit else "Not committed"
            item = QListWidgetItem(f"[{status}] {change.change_type.name.title()}  {change.path}")
            item.setData(_FILE_CHANGE_ROLE, change)
            self._file_list.addItem(item)

        if changes:
            self._file_list.setCurrentRow(0)

    def _on_diff_toggled(self, checked: bool) -> None:
        self._diff_stack.setCurrentIndex(1 if checked else 0)
        self._diff_toggle_button.setText("Unified" if checked else "Side-by-side")

    def _on_file_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            self._unified_diff_view.clear_diff()
            self._side_by_side_diff_view.clear_diff()
            return
        change: FileChange = current.data(_FILE_CHANGE_ROLE)

        try:
            diff = self._adapter.compute_diff(change)
        except Exception as exc:
            QMessageBox.warning(self, "Show Changes failed", f"Failed to compute diff: {exc}")
            return

        self._unified_diff_view.set_diff(diff, str(change.path))
        self._side_by_side_diff_view.set_diff(diff, str(change.path))
