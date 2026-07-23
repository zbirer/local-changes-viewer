from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from local_changes_viewer.core.domain.workspace import Workspace

_ITEM_DATA_ROLE = Qt.ItemDataRole.UserRole + 1


class AggregateChangeList(QListWidget):
    file_selected = Signal(object, object)  # repo_path: Path, change: FileChange

    def __init__(self) -> None:
        super().__init__()
        self._workspace: Workspace | None = None
        self._scope_repo_path: Path | None = None
        self._scope_prefix: Path | None = None
        self.currentItemChanged.connect(self._on_current_item_changed)

    def set_workspace(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._rebuild()

    def set_scope(self, repo_path: Path | None, prefix: Path | None = None) -> None:
        self._scope_repo_path = repo_path
        self._scope_prefix = prefix
        self._rebuild()

    def clear_scope(self) -> None:
        self.set_scope(None, None)

    def _rebuild(self) -> None:
        self.clear()
        if self._workspace is None:
            return
        for repo in self._workspace.repositories:
            if self._scope_repo_path is not None and repo.path != self._scope_repo_path:
                continue
            for change in repo.changes:
                if self._scope_prefix is not None:
                    prefix_parts = self._scope_prefix.parts
                    if change.path.parts[: len(prefix_parts)] != prefix_parts:
                        continue
                item = QListWidgetItem(f"{repo.name}/{change.path}")
                item.setData(_ITEM_DATA_ROLE, (repo.path, change))
                self.addItem(item)

    def _on_current_item_changed(self, current: QListWidgetItem, _previous) -> None:
        if current is None:
            return
        repo_path, change = current.data(_ITEM_DATA_ROLE)
        self.file_selected.emit(repo_path, change)
