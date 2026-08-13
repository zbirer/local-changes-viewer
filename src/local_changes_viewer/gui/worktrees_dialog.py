from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.worktree_info import WorktreeInfo
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.gui.worktree_changes_dialog import WorktreeChangesDialog

_COLUMNS = ("Path", "Branch", "Last Commit / Modified", "Unpushed Changes", "Created")
_WORKTREE_ROLE = Qt.ItemDataRole.UserRole


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

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        for column in range(len(_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self._table.setSortingEnabled(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu_requested)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)

        self._row_worktrees: list[WorktreeInfo] = []

        layout = QVBoxLayout(self)
        layout.addWidget(self._table)

        self._reload()

    def _reload(self) -> None:
        try:
            details = self._adapter_factory(self._repo_path).list_worktree_details()
        except Exception as exc:
            QMessageBox.warning(self, "List Worktrees failed", f"Failed to list worktrees: {exc}")
            details = []

        self._row_worktrees = list(details)
        self._table.setSortingEnabled(False)
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
                if column == 0:
                    item.setData(_WORKTREE_ROLE, info)
                self._table.setItem(row, column, item)

        if not details:
            self._table.setRowCount(1)
            placeholder = QTableWidgetItem("No linked worktrees")
            self._table.setItem(0, 0, placeholder)
            self._table.setSpan(0, 0, 1, len(_COLUMNS))

        self._table.setSortingEnabled(True)
        self._table.resizeColumnsToContents()

    def _worktree_at_row(self, row: int) -> WorktreeInfo | None:
        item = self._table.item(row, 0)
        if item is None:
            return None
        return item.data(_WORKTREE_ROLE)

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

    def _on_cell_double_clicked(self, row: int, _column: int) -> None:
        worktree = self._worktree_at_row(row)
        if worktree is None:
            return
        self._on_show_changes(worktree)

    def _on_context_menu_requested(self, position) -> None:
        row = self._table.rowAt(position.y())
        if row < 0:
            return
        worktree = self._worktree_at_row(row)
        if worktree is None:
            return

        menu = QMenu(self)
        menu.addAction("Delete", lambda: self._on_delete(worktree))
        menu.addAction("Show Changes", lambda: self._on_show_changes(worktree))
        menu.addAction("Copy Path", lambda: self._on_copy_path(worktree))
        menu.exec(self._table.viewport().mapToGlobal(position))

    def _on_show_changes(self, worktree: WorktreeInfo) -> None:
        dialog = WorktreeChangesDialog(
            worktree.path, adapter_factory=self._adapter_factory, parent=self
        )
        dialog.exec()

    def _on_copy_path(self, worktree: WorktreeInfo) -> None:
        QGuiApplication.clipboard().setText(str(worktree.path))
