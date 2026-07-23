from pathlib import Path

from PySide6.QtGui import QStandardItem, QStandardItemModel

from local_changes_viewer.core.domain.workspace import Workspace


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
            label = f"{repo.name}  [{branch.branch_name}, +{branch.ahead}/-{branch.behind}]"
            repo_item = QStandardItem(label)
            repo_item.setEditable(False)
            root.appendRow(repo_item)
            self._add_changes(repo_item, repo.changes)

    @staticmethod
    def _add_changes(repo_item: QStandardItem, changes) -> None:
        dir_items: dict[Path, QStandardItem] = {}
        for change in changes:
            parts = change.path.parts
            parent_item = repo_item
            accumulated = Path()
            for part in parts[:-1]:
                accumulated = accumulated / part
                dir_item = dir_items.get(accumulated)
                if dir_item is None:
                    dir_item = QStandardItem(part)
                    dir_item.setEditable(False)
                    parent_item.appendRow(dir_item)
                    dir_items[accumulated] = dir_item
                parent_item = dir_item

            file_item = QStandardItem(parts[-1] if parts else str(change.path))
            file_item.setEditable(False)
            parent_item.appendRow(file_item)
