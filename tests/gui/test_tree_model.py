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


def test_sync_nested_repos_does_not_crash_when_repo_has_both_a_direct_worktree_and_a_filesystem_nested_repo(
    qapp,
) -> None:
    """Regression test for a RuntimeError crash ("libshiboken: Internal C++
    object (PySide6.QtGui.QStandardItem) already deleted") when a repo has two
    kinds of nested children at once: a git worktree discovered via
    logical_parent_path (which lives in a sibling directory, so it becomes a
    DIRECT child of the repo_item with no intermediate directory node) and a
    second nested repo that lives at a filesystem subpath (which gets an
    intermediate "nested-dir::" container item appended as a sibling under the
    same repo_item).

    _sync_nested_repos processes each nested child's container via
    _sync_level. When it processed the worktree's container (the repo_item
    itself), _sync_level's stale-row removal treated the *other* child's
    "nested-dir::" container item -- a sibling row on the same repo_item, keyed
    with a format that never matches a repo path -- as a stale repo row and
    removed it. _sync_nested_repos then tried to recurse into that
    already-deleted container for the filesystem-nested repo, crashing with
    the RuntimeError above. This exercises that exact shape in one
    update_workspace call, without needing to trigger a real Qt deletion
    outside the model.
    """
    repo_path = Path("/repos/parent-repo")
    worktree_path = Path("/repos/parent-repo-worktree-feature")
    nested_repo_path = repo_path / "vendor" / "nested-repo"

    parent_repo = Repository(
        path=repo_path,
        name="parent-repo",
        branch_status=_BRANCH,
    )
    worktree_repo = Repository(
        path=worktree_path,
        name="parent-repo-worktree-feature",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("b.txt"), change_type=ChangeType.MODIFIED)],
        logical_parent_path=repo_path,
    )
    nested_repo = Repository(
        path=nested_repo_path,
        name="nested-repo",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("c.txt"), change_type=ChangeType.MODIFIED)],
    )
    workspace = Workspace(
        root_path=Path("/repos"),
        repositories=[parent_repo, worktree_repo, nested_repo],
    )

    model = RepoTreeModel()
    # This must not raise RuntimeError.
    model.update_workspace(workspace)

    root = model.invisibleRootItem()
    assert root.rowCount() == 1
    parent_item = root.child(0)
    assert parent_item.data(NODE_KEY_ROLE) == str(repo_path)

    child_keys = {
        parent_item.child(row).data(NODE_KEY_ROLE) for row in range(parent_item.rowCount())
    }
    assert str(worktree_path) in child_keys
    assert any(
        key is not None and str(key).startswith("nested-dir::") for key in child_keys
    ), "the nested repo's intermediate directory node must survive the sync"
