from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel

from local_changes_viewer.core.domain.file_change import ChangeType
from local_changes_viewer.core.domain.workspace import Workspace

_CHANGE_COLORS = {
    ChangeType.MODIFIED: QColor("#3B82F6"),
    ChangeType.ADDED: QColor("#22C55E"),
    ChangeType.DELETED: QColor("#EF4444"),
    ChangeType.RENAMED: QColor("#A855F7"),
    ChangeType.UNTRACKED: QColor("#6B7280"),
    ChangeType.IGNORED: QColor("#9CA3AF"),
}

NODE_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
FILE_CHANGE_ROLE = Qt.ItemDataRole.UserRole + 2
REPO_PATH_ROLE = Qt.ItemDataRole.UserRole + 3


class RepoTreeModel(QStandardItemModel):
    def __init__(self) -> None:
        super().__init__()
        self.setHorizontalHeaderLabels(["Name"])

    def set_workspace(self, workspace: Workspace) -> None:
        self.clear()
        self.setHorizontalHeaderLabels(["Name"])
        root = self.invisibleRootItem()
        for repo in workspace.repositories:
            branch = repo.branch_status
            label = (
                f"{repo.name}  [{branch.branch_name}, +{branch.ahead}/-{branch.behind}]"
                f"  ({len(repo.changes)})"
            )
            repo_item = QStandardItem(label)
            repo_item.setEditable(False)
            repo_item.setData(str(repo.path), NODE_KEY_ROLE)
            root.appendRow(repo_item)
            self._add_changes(repo_item, repo)

    @staticmethod
    def _add_changes(repo_item: QStandardItem, repo) -> None:
        changes = repo.changes
        dir_items: dict[Path, QStandardItem] = {}
        dir_counts: dict[Path, int] = {}
        for change in changes:
            parts = change.path.parts
            accumulated = Path()
            for part in parts[:-1]:
                accumulated = accumulated / part
                dir_counts[accumulated] = dir_counts.get(accumulated, 0) + 1

        for change in changes:
            parts = change.path.parts
            parent_item = repo_item
            accumulated = Path()
            for part in parts[:-1]:
                accumulated = accumulated / part
                dir_item = dir_items.get(accumulated)
                if dir_item is None:
                    dir_item = QStandardItem(f"{part}  ({dir_counts[accumulated]})")
                    dir_item.setEditable(False)
                    dir_item.setData(f"{repo.path}::{accumulated}", NODE_KEY_ROLE)
                    parent_item.appendRow(dir_item)
                    dir_items[accumulated] = dir_item
                parent_item = dir_item

            file_name = parts[-1] if parts else str(change.path)
            if change.is_directory:
                file_name += "/"
            file_item = QStandardItem(file_name)
            file_item.setEditable(False)
            file_item.setForeground(QBrush(_CHANGE_COLORS[change.change_type]))
            file_item.setData(change, FILE_CHANGE_ROLE)
            file_item.setData(str(repo.path), REPO_PATH_ROLE)
            parent_item.appendRow(file_item)
