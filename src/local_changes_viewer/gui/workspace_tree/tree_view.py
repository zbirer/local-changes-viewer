from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import QTreeView

from local_changes_viewer.gui import applog
from local_changes_viewer.gui.settings import AppSettings
from local_changes_viewer.gui.workspace_tree.tree_model import (
    FILE_CHANGE_ROLE,
    NODE_KEY_ROLE,
    REPO_PATH_ROLE,
    RepoTreeModel,
)


class RepoTreeView(QTreeView):
    file_selected = Signal(object, object)  # repo_path: Path, change: FileChange

    def __init__(self, settings: AppSettings) -> None:
        super().__init__()
        self._settings = settings
        self._programmatic_change = False
        self._model = RepoTreeModel()
        self._proxy = QSortFilterProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setRecursiveFilteringEnabled(True)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setModel(self._proxy)
        self.setHeaderHidden(True)
        self.collapsed.connect(self._on_collapsed)
        self.expanded.connect(self._on_expanded)
        self.selectionModel().currentChanged.connect(self._on_current_changed)

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        change = current.data(FILE_CHANGE_ROLE)
        repo_path = current.data(REPO_PATH_ROLE)
        if change is not None and repo_path is not None:
            self.file_selected.emit(repo_path, change)

    def set_workspace(self, workspace) -> None:
        self._programmatic_change = True
        self._model.set_workspace(workspace)
        self.expandAll()
        self._restore_collapsed_state()
        self._programmatic_change = False

    def set_filter_text(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        if text:
            self._programmatic_change = True
            self.expandAll()
            self._programmatic_change = False

    def collapse_all(self) -> None:
        self._programmatic_change = True
        self.collapseAll()
        self._programmatic_change = False
        self._settings.set_collapsed_node_keys(self._collect_all_keys(QModelIndex()))

    def expand_all(self) -> None:
        self._programmatic_change = True
        self.expandAll()
        self._programmatic_change = False
        self._settings.set_collapsed_node_keys(set())

    def _collect_all_keys(self, parent: QModelIndex) -> set[str]:
        keys: set[str] = set()
        for row in range(self._proxy.rowCount(parent)):
            index = self._proxy.index(row, 0, parent)
            key = index.data(NODE_KEY_ROLE)
            if key is not None:
                keys.add(key)
            keys |= self._collect_all_keys(index)
        return keys

    def _restore_collapsed_state(self) -> None:
        collapsed_keys = self._settings.collapsed_node_keys()
        applog.log(f"_restore_collapsed_state: collapsed_keys={collapsed_keys!r}")
        if not collapsed_keys:
            return
        all_keys = self._collect_all_keys(QModelIndex())
        applog.log(f"_restore_collapsed_state: current tree node keys={all_keys!r}")
        self._for_each_index(QModelIndex(), collapsed_keys)

    def _for_each_index(self, parent: QModelIndex, collapsed_keys: set[str]) -> None:
        for row in range(self._proxy.rowCount(parent)):
            index = self._proxy.index(row, 0, parent)
            key = index.data(NODE_KEY_ROLE)
            if key is not None and key in collapsed_keys:
                applog.log(f"_for_each_index: collapsing key={key!r}")
                self.collapse(index)
            self._for_each_index(index, collapsed_keys)

    def _on_collapsed(self, index: QModelIndex) -> None:
        key = index.data(NODE_KEY_ROLE)
        applog.log(f"_on_collapsed: key={key!r} programmatic={self._programmatic_change}")
        if key is None or self._programmatic_change:
            return
        keys = self._settings.collapsed_node_keys()
        keys.add(key)
        self._settings.set_collapsed_node_keys(keys)

    def _on_expanded(self, index: QModelIndex) -> None:
        key = index.data(NODE_KEY_ROLE)
        applog.log(f"_on_expanded: key={key!r} programmatic={self._programmatic_change}")
        if key is None or self._programmatic_change:
            return
        keys = self._settings.collapsed_node_keys()
        keys.discard(key)
        self._settings.set_collapsed_node_keys(keys)
