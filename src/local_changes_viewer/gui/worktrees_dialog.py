from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.worktree_info import WorktreeInfo
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter

_COLUMNS = ("Path", "Branch", "Last Commit / Modified", "Unpushed Changes", "Created")
_PATH_COLUMN = 0
_DELETE_COLUMN = len(_COLUMNS)


class WorktreesDialog(QDialog):
    def __init__(
        self, repo_path: Path, adapter_factory=GitRepoAdapter, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._repo_path = repo_path
        self._adapter_factory = adapter_factory
        self.deleted_any = False
        self.setWindowTitle(f"Worktrees — {repo_path.name}")
        parent_width = parent.width() if parent is not None else 900
        self.resize(int(parent_width * 0.7), 400)

        self._table = QTableWidget(0, len(_COLUMNS) + 1)
        self._table.setHorizontalHeaderLabels((*_COLUMNS, ""))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_PATH_COLUMN, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_DELETE_COLUMN, QHeaderView.ResizeMode.ResizeToContents)

        layout = QVBoxLayout(self)
        layout.addWidget(self._table)

        self._reload()

    def _reload(self) -> None:
        try:
            details = self._adapter_factory(self._repo_path).list_worktree_details()
        except Exception as exc:
            QMessageBox.warning(self, "List Worktrees failed", f"Failed to list worktrees: {exc}")
            details = []

        self._table.setRowCount(len(details))
        for row, info in enumerate(details):
            values = (
                str(info.path),
                info.branch_name,
                info.last_activity.strftime("%Y-%m-%d %H:%M") if info.last_activity else "—",
                "Yes" if info.has_unpushed_changes else "No",
                info.created_at.strftime("%Y-%m-%d %H:%M") if info.created_at else "—",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self._table.setItem(row, column, item)

            delete_button = QPushButton("🗑")
            delete_button.setToolTip(f"Delete worktree at {info.path}")
            delete_button.clicked.connect(lambda _checked=False, wt=info: self._on_delete(wt))
            button_container = QWidget()
            button_layout = QHBoxLayout(button_container)
            button_layout.setContentsMargins(0, 0, 0, 0)
            button_layout.addWidget(delete_button)
            self._table.setCellWidget(row, _DELETE_COLUMN, button_container)

        if not details:
            self._table.setRowCount(1)
            placeholder = QTableWidgetItem("No linked worktrees")
            self._table.setItem(0, 0, placeholder)
            self._table.setSpan(0, 0, 1, len(_COLUMNS) + 1)

    def _on_delete(self, worktree: WorktreeInfo) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete Worktree",
            f"Delete the worktree at:\n{worktree.path}\n\n"
            "This removes its files from disk. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        adapter = self._adapter_factory(self._repo_path)
        try:
            adapter.remove_worktree(worktree.path)
        except Exception as exc:
            force_confirm = QMessageBox.question(
                self,
                "Delete Worktree",
                f"Failed to delete cleanly (it may have uncommitted or unpushed "
                f"changes):\n{exc}\n\nForce delete anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if force_confirm != QMessageBox.StandardButton.Yes:
                return
            try:
                adapter.remove_worktree(worktree.path, force=True)
            except Exception as force_exc:
                QMessageBox.warning(
                    self, "Delete Worktree failed", f"Failed to delete worktree: {force_exc}"
                )
                return

        self.deleted_any = True
        self._reload()
