from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.commit_log_entry import CommitLogEntry
from local_changes_viewer.core.domain.file_change import FileChange
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.gui.diff_view.side_by_side_view import SideBySideView
from local_changes_viewer.gui.diff_view.unified_view import UnifiedView

_FILE_CHANGE_ROLE = Qt.ItemDataRole.UserRole

_DEFAULT_COMMIT_LIMIT = 5
_MIN_COMMIT_LIMIT = 2
_MAX_COMMIT_LIMIT = 20
_COMMIT_COLUMNS = ("Time", "Hash", "Message")


class CommitLogDialog(QDialog):
    def __init__(self, repo_path: Path, adapter_factory=GitRepoAdapter, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repo_path = repo_path
        self._adapter = adapter_factory(repo_path)
        self._commits: list[CommitLogEntry] = []
        self.setWindowTitle(f"Commit Log — {repo_path.name}")
        parent_width = parent.width() if parent is not None else 1100
        self.resize(int(parent_width * 0.8), 600)

        self._commit_table = QTableWidget(0, len(_COMMIT_COLUMNS))
        self._commit_table.setHorizontalHeaderLabels(_COMMIT_COLUMNS)
        self._commit_table.verticalHeader().setVisible(False)
        self._commit_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._commit_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._commit_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._commit_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self._commit_table.currentCellChanged.connect(self._on_commit_cell_changed)

        self._commit_count_slider = QSlider(Qt.Orientation.Horizontal)
        self._commit_count_slider.setRange(_MIN_COMMIT_LIMIT, _MAX_COMMIT_LIMIT)
        self._commit_count_slider.setValue(_DEFAULT_COMMIT_LIMIT)
        self._commit_count_label = QLabel(str(_DEFAULT_COMMIT_LIMIT))
        self._commit_count_slider.valueChanged.connect(self._on_commit_count_changed)

        count_row = QWidget()
        count_layout = QHBoxLayout(count_row)
        count_layout.setContentsMargins(0, 0, 0, 0)
        count_layout.addWidget(QLabel("Commits to show:"))
        count_layout.addWidget(self._commit_count_slider)
        count_layout.addWidget(self._commit_count_label)

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

        commit_panel = self._labeled_panel("Commits", self._commit_table, extra_header=count_row)
        file_panel = self._labeled_panel("Files Changed", self._file_list)
        diff_panel = self._labeled_panel("Diff", self._diff_stack, extra_header=diff_header)

        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.addWidget(commit_panel)
        left_splitter.addWidget(file_panel)
        left_splitter.setStretchFactor(0, 3)
        left_splitter.setStretchFactor(1, 2)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_splitter)
        splitter.addWidget(diff_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        layout = QVBoxLayout(self)
        layout.addWidget(splitter)

        self._load_commits()

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

    def _on_commit_count_changed(self, value: int) -> None:
        self._commit_count_label.setText(str(value))
        self._load_commits()

    def _load_commits(self) -> None:
        try:
            commits = self._adapter.get_recent_commits(limit=self._commit_count_slider.value())
        except Exception as exc:
            QMessageBox.warning(self, "Show Log failed", f"Failed to read commit log: {exc}")
            return

        self._commits = commits
        self._commit_table.setRowCount(len(commits))
        for row, commit in enumerate(commits):
            values = (
                commit.committed_datetime.strftime("%Y-%m-%d %H:%M"),
                commit.short_hexsha,
                commit.message,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                tooltip = commit.full_message if column == 2 and commit.full_message else value
                item.setToolTip(tooltip)
                self._commit_table.setItem(row, column, item)

        if commits:
            self._commit_table.selectRow(0)

    def _on_diff_toggled(self, checked: bool) -> None:
        self._diff_stack.setCurrentIndex(1 if checked else 0)
        self._diff_toggle_button.setText("Unified" if checked else "Side-by-side")

    def _on_commit_cell_changed(
        self, current_row: int, _current_column: int, _previous_row: int, _previous_column: int
    ) -> None:
        self._file_list.clear()
        self._unified_diff_view.clear_diff()
        self._side_by_side_diff_view.clear_diff()
        if current_row < 0 or current_row >= len(self._commits):
            return
        commit = self._commits[current_row]

        try:
            changes = self._adapter.get_commit_files(commit.hexsha)
        except Exception as exc:
            QMessageBox.warning(self, "Show Log failed", f"Failed to read commit files: {exc}")
            return

        for change in changes:
            text = f"{change.change_type.name.title()}  {change.path}"
            item = QListWidgetItem(text)
            item.setToolTip(text)
            item.setData(_FILE_CHANGE_ROLE, change)
            self._file_list.addItem(item)

        if changes:
            self._file_list.setCurrentRow(0)

    def _on_file_selected(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            self._unified_diff_view.clear_diff()
            self._side_by_side_diff_view.clear_diff()
            return
        current_row = self._commit_table.currentRow()
        if current_row < 0 or current_row >= len(self._commits):
            return
        commit = self._commits[current_row]
        change: FileChange = current.data(_FILE_CHANGE_ROLE)

        try:
            diff = self._adapter.get_commit_file_diff(
                commit.hexsha, change.path, change.old_path
            )
        except Exception as exc:
            QMessageBox.warning(self, "Show Log failed", f"Failed to compute diff: {exc}")
            return

        self._unified_diff_view.set_diff(diff, str(change.path))
        self._side_by_side_diff_view.set_diff(diff, str(change.path))
