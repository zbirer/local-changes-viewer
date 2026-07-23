from PySide6.QtCore import QSortFilterProxyModel, Qt
from PySide6.QtWidgets import QTreeView

from local_changes_viewer.gui.workspace_tree.tree_model import RepoTreeModel


class RepoTreeView(QTreeView):
    def __init__(self) -> None:
        super().__init__()
        self._model = RepoTreeModel()
        self._proxy = QSortFilterProxyModel()
        self._proxy.setSourceModel(self._model)
        self._proxy.setRecursiveFilteringEnabled(True)
        self._proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setModel(self._proxy)
        self.setHeaderHidden(True)

    def set_workspace(self, workspace) -> None:
        self._model.set_workspace(workspace)
        self.expandAll()

    def set_filter_text(self, text: str) -> None:
        self._proxy.setFilterFixedString(text)
        if text:
            self.expandAll()
