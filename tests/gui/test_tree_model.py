import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
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


def test_partition_deduplicates_repositories_sharing_a_path(qapp) -> None:
    """Regression test for RepoTreeModel._partition treating duplicate
    Repository entries as each other's parent: when two distinct Repository
    objects share the same path, `repo.path.relative_to(other.path)` never
    raises (a path is trivially relative to itself), so the old identity-only
    guard (`if other is repo`) let each duplicate be inferred as the other's
    parent. Both then dropped out of `roots`, and with every repo duplicated
    the tree rendered completely empty with no error. _partition must now
    de-duplicate by path before computing parentage, so a workspace with a
    duplicated repo path still produces exactly one top-level row for it.
    """
    repo_path = Path("/repos/dup-repo")
    duplicate_a = Repository(
        path=repo_path,
        name="dup-repo",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("a.txt"), change_type=ChangeType.MODIFIED)],
    )
    duplicate_b = Repository(
        path=repo_path,
        name="dup-repo",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("a.txt"), change_type=ChangeType.MODIFIED)],
    )
    workspace = Workspace(root_path=Path("/repos"), repositories=[duplicate_a, duplicate_b])

    model = RepoTreeModel()
    model.set_workspace(workspace)

    assert model.has_rows() is True
    root = model.invisibleRootItem()
    assert root.rowCount() == 1
    assert root.child(0).data(NODE_KEY_ROLE) == str(repo_path)
