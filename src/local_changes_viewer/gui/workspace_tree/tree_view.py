from PySide6.QtWidgets import QTreeView

from local_changes_viewer.gui.workspace_tree.tree_model import RepoTreeModel


class RepoTreeView(QTreeView):
    def __init__(self) -> None:
        super().__init__()
        self._model = RepoTreeModel()
        self.setModel(self._model)
        self.setHeaderHidden(True)

    def set_workspace(self, workspace) -> None:
        self._model.set_workspace(workspace)
        self.expandAll()
