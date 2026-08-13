import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import git
import pytest
from PySide6.QtWidgets import QApplication

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.services.workspace_scanner_service import WorkspaceScannerService
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


def _init_repo(repo_path: Path) -> git.Repo:
    repo_path.mkdir(parents=True, exist_ok=True)
    repo = git.Repo.init(repo_path, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test User")
        cw.set_value("user", "email", "test@example.com")
    (repo_path / "README.md").write_text("hello\n")
    repo.index.add(["README.md"])
    repo.index.commit("init")
    return repo


def _find_repo_item_by_key(item, key: str):
    """Recursively locate the repo-kind QStandardItem whose NODE_KEY_ROLE is
    `key`, so a test can inspect its displayed text() (e.g. for the
    "[external] " prefix) without depending on tree depth/shape."""
    from local_changes_viewer.gui.workspace_tree.tree_model import _NODE_KIND_REPO, _NODE_KIND_ROLE

    for row in range(item.rowCount()):
        child = item.child(row)
        if child.data(_NODE_KIND_ROLE) == _NODE_KIND_REPO and child.data(NODE_KEY_ROLE) == key:
            return child
        found = _find_repo_item_by_key(child, key)
        if found is not None:
            return found
    return None


def _all_repo_keys(item) -> set:
    """Every NODE_KEY_ROLE value under `item` whose node kind is a repo row
    (recursively), used below to check a worktree surfaced as its own nested
    repo item rather than merely appearing as a filesystem child under some
    other row."""
    from local_changes_viewer.gui.workspace_tree.tree_model import _NODE_KIND_REPO, _NODE_KIND_ROLE

    keys = set()
    for row in range(item.rowCount()):
        child = item.child(row)
        if child.data(_NODE_KIND_ROLE) == _NODE_KIND_REPO:
            keys.add(child.data(NODE_KEY_ROLE))
        keys |= _all_repo_keys(child)
    return keys


def test_set_workspace_renders_clean_worktree_as_nested_repo_row(qapp, tmp_path: Path) -> None:
    """Regression test for a real-world bug report: a git worktree nested
    inside its parent repo's own directory tree (e.g. `.claude/worktrees/x`,
    the exact shape `git worktree add` produces for an in-tree worktree) that
    currently has NO uncommitted changes must still render as its own nested
    repo row in the tree -- the same way `WorktreesDialog` (which queries `git
    worktree list` directly) always lists it regardless of its dirty state.

    This goes through the REAL WorkspaceScannerService.scan() (no fakes/mocks)
    against a REAL git repo + real `git worktree add`, then feeds the
    resulting Workspace into a REAL RepoTreeModel, because the previous
    regression test for this class of bug
    (test_scan_discovers_gitignored_worktree_as_nested_repo_but_not_other_ignored_dirs
    in test_workspace_scanner_service.py) only asserted on the scanner's
    Workspace.repositories list -- it never checked that RepoTreeModel
    actually renders the worktree as a nested tree row, which is where the
    real bug lived: _sync_nested_repos unconditionally dropped any nested
    repo (worktree or not) with no changes anywhere in its own subtree,
    regardless of the separate, user-facing "Hide repos without changes"
    setting (see F35) which already implements this correctly and is off by
    default.
    """
    repo_path = tmp_path / "dashboard"
    repo = _init_repo(repo_path)

    worktree_path = repo_path / ".claude" / "worktrees" / "vibrant-sinoussi-809799"
    repo.git.worktree("add", str(worktree_path), "-b", "vibrant-sinoussi-809799")
    # Deliberately leave the worktree clean (no edits) -- this is the exact
    # shape that was hidden by the bug.

    workspace = WorkspaceScannerService().scan(tmp_path)

    model = RepoTreeModel()
    model.set_workspace(workspace)

    root = model.invisibleRootItem()
    assert root.rowCount() == 1, "the parent repo should be the only top-level row"
    parent_item = root.child(0)
    assert parent_item.data(NODE_KEY_ROLE) == str(repo_path)

    assert str(worktree_path) in _all_repo_keys(parent_item), (
        "a clean (no-changes) nested worktree must still render as its own "
        "nested repo row, matching WorktreesDialog's listing"
    )


def test_set_workspace_renders_all_worktrees_when_repo_has_several(
    qapp, tmp_path: Path
) -> None:
    """The real dashboard repo behind the bug report has several linked
    worktrees at once (some with changes, some clean). Reproduces that exact
    shape: a repo with two nested worktrees under `.claude/worktrees/`, one
    dirty and one clean, plus a third nested under the older `.worktrees/`
    layout -- all three must render as nested repo rows regardless of which
    ones have changes.
    """
    repo_path = tmp_path / "dashboard"
    repo = _init_repo(repo_path)

    dirty_worktree = repo_path / ".claude" / "worktrees" / "bugbot-silent-wrong-answer"
    repo.git.worktree("add", str(dirty_worktree), "-b", "bugbot-silent-wrong-answer")
    (dirty_worktree / "scratch.txt").write_text("wip\n")

    clean_worktree = repo_path / ".claude" / "worktrees" / "vibrant-sinoussi-809799"
    repo.git.worktree("add", str(clean_worktree), "-b", "vibrant-sinoussi-809799")

    legacy_worktree = repo_path / ".worktrees" / "legacy-feature"
    repo.git.worktree("add", str(legacy_worktree), "-b", "legacy-feature")

    workspace = WorkspaceScannerService().scan(tmp_path)
    assert {r.name for r in workspace.repositories} == {
        "dashboard",
        "bugbot-silent-wrong-answer",
        "vibrant-sinoussi-809799",
        "legacy-feature",
    }

    model = RepoTreeModel()
    model.set_workspace(workspace)

    root = model.invisibleRootItem()
    assert root.rowCount() == 1
    parent_item = root.child(0)
    repo_keys = _all_repo_keys(parent_item)

    assert str(dirty_worktree) in repo_keys
    assert str(clean_worktree) in repo_keys
    assert str(legacy_worktree) in repo_keys


def test_set_workspace_renders_internal_worktree_name_without_external_prefix(
    qapp, tmp_path: Path
) -> None:
    """A worktree added inside its parent repo's own directory tree (e.g.
    `.claude/worktrees/name`, matching `dashboard`'s real `vibrant-sinoussi-809799`
    worktree) lives on disk *inside* its parent, so it must render with its
    plain name -- no `[external] ` prefix -- in the tree label.
    """
    repo_path = tmp_path / "dashboard"
    repo = _init_repo(repo_path)

    internal_worktree = repo_path / ".claude" / "worktrees" / "vibrant-sinoussi-809799"
    repo.git.worktree("add", str(internal_worktree), "-b", "vibrant-sinoussi-809799")

    workspace = WorkspaceScannerService().scan(tmp_path)

    model = RepoTreeModel()
    model.set_workspace(workspace)

    root = model.invisibleRootItem()
    parent_item = root.child(0)
    worktree_item = _find_repo_item_by_key(parent_item, str(internal_worktree))
    assert worktree_item is not None
    assert worktree_item.text().startswith("vibrant-sinoussi-809799")
    assert "[external]" not in worktree_item.text()


def test_set_workspace_renders_external_worktree_name_with_external_prefix(
    qapp, tmp_path: Path
) -> None:
    """A worktree added OUTSIDE its parent repo's own directory tree (e.g.
    a sibling directory, matching `dashboard`'s real
    `~/dev/.worktrees/dashboard-eh-12404` worktree) is physically external to
    its parent even though it's still logically nested under it in the tree,
    so its label must be prefixed with `[external] ` to distinguish it from
    an in-tree worktree.
    """
    workspace_root = tmp_path / "workspace"
    repo_path = workspace_root / "dashboard"
    repo = _init_repo(repo_path)

    # Sibling of `workspace_root`/`repo_path`, entirely outside the parent
    # repo's own directory -- the exact shape of the real external worktrees
    # (`~/dev/.worktrees/dashboard-eh-12404`, `~/dev/worktrees/dashboard-eh-12404`).
    external_worktree = tmp_path / "external-worktrees" / "dashboard-bugbot-security-rules"
    repo.git.worktree(
        "add", str(external_worktree), "-b", "dashboard-bugbot-security-rules"
    )

    workspace = WorkspaceScannerService().scan(workspace_root)

    model = RepoTreeModel()
    model.set_workspace(workspace)

    root = model.invisibleRootItem()
    parent_item = root.child(0)
    assert parent_item.data(NODE_KEY_ROLE) == str(repo_path)

    worktree_item = _find_repo_item_by_key(parent_item, str(external_worktree))
    assert worktree_item is not None
    assert worktree_item.text().startswith("[external] dashboard-bugbot-security-rules")
