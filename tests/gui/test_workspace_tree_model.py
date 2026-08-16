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
    # A plain (non-worktree) repo root has no logical_parent_path, so it
    # never grows the worktree-only folder-timestamp lines (see the
    # worktree-specific test below).
    assert "Folder created:" not in tooltip
    assert "Folder last modified:" not in tooltip


def _find_repo_item_by_key(item, key: str):
    """Recursively locate the repo-kind QStandardItem whose NODE_KEY_ROLE is
    `key`, so the test doesn't depend on the nested worktree's exact depth
    under its parent."""
    for row in range(item.rowCount()):
        child = item.child(row)
        if child.data(NODE_KEY_ROLE) == key:
            return child
        found = _find_repo_item_by_key(child, key)
        if found is not None:
            return found
    return None


def test_worktree_row_tooltip_includes_folder_created_and_modified(qapp, tmp_path) -> None:
    """A worktree row's tooltip (identified by logical_parent_path, F6) also
    surfaces its own folder's creation and last-modification times, distinct
    from the branch/commit history already summarized in the tooltip."""
    repo_path = tmp_path / "parent-repo"
    worktree_path = tmp_path / "parent-repo-worktree-feature"
    repo_path.mkdir()
    worktree_path.mkdir()

    parent_repo = Repository(path=repo_path, name="parent-repo", branch_status=_BRANCH)
    worktree_repo = Repository(
        path=worktree_path,
        name="parent-repo-worktree-feature",
        branch_status=_BRANCH,
        logical_parent_path=repo_path,
    )
    workspace = Workspace(root_path=tmp_path, repositories=[parent_repo, worktree_repo])

    model = RepoTreeModel()
    model.set_workspace(workspace)

    root = model.invisibleRootItem()
    worktree_item = _find_repo_item_by_key(root, str(worktree_path))
    assert worktree_item is not None
    tooltip = worktree_item.data(Qt.ItemDataRole.ToolTipRole)
    assert "Folder created:" in tooltip
    assert "Folder last modified:" in tooltip
