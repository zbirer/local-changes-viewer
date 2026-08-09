import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.gui.workspace_tree.tree_model import NODE_KEY_ROLE, RepoTreeModel

_BRANCH = BranchStatus(branch_name="main", ahead=0, behind=0)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_repo_row_tooltip_includes_absolute_path(qapp) -> None:
    """The repo-root row's tooltip should surface the repository folder's
    absolute path, in addition to the existing name/status/branch/PR lines."""
    repo_path = Path("/repos/example-repo")
    repo = Repository(
        path=repo_path,
        name="example-repo",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("a.txt"), change_type=ChangeType.MODIFIED)],
    )
    workspace = Workspace(root_path=Path("/repos"), repositories=[repo])

    model = RepoTreeModel()
    model.set_workspace(workspace)

    root = model.invisibleRootItem()
    repo_item = root.child(0)
    assert repo_item.data(NODE_KEY_ROLE) == str(repo_path)

    tooltip = repo_item.data(Qt.ItemDataRole.ToolTipRole)
    assert f"Path: {str(repo_path)}" in tooltip
    # The path line should not disturb the existing tooltip content.
    assert tooltip.startswith(f"Name: {repo.name}\n")
