from pathlib import Path

from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.gui import applog
from local_changes_viewer.gui.workspace_tree.tree_model import FILE_CHANGE_ROLE, REPO_PATH_ROLE


def find_tree_index(
    model, repo_path: Path, change: FileChange, parent: QModelIndex = QModelIndex()
) -> QModelIndex:
    """Recursively searches the folder tree's (proxied) model for the
    row carrying `change`, using the same FILE_CHANGE_ROLE/REPO_PATH_ROLE
    the tree model already exposes -- plain public QAbstractItemModel
    API (rowCount/index/data), not a reach into tree_view.py's private
    attributes.
    """
    for row in range(model.rowCount(parent)):
        index = model.index(row, 0, parent)
        if index.data(FILE_CHANGE_ROLE) is change and index.data(REPO_PATH_ROLE) == str(
            repo_path
        ):
            return index
        found = find_tree_index(model, repo_path, change, index)
        if found.isValid():
            return found
    return QModelIndex()


def edit_target(repo_path: Path, change: FileChange) -> tuple[Path | None, str | None]:
    """Whether `change` can be edited in place, and if not, why -- as a
    single (path, reason) pair so the two can never drift apart.
    Exactly one of the two is None.

    `is_unpushed_commit` is checked first: that diff is old_ref=<upstream>,
    new_ref=HEAD (see git_repo_adapter.compute_diff), so the file on disk
    is the CURRENT working tree, not either side of the displayed diff --
    loading it into the edit pane would show content that matches
    neither ref, and Save would silently overwrite it with something
    unrelated to what the diff shown. This must be checked before
    DELETED/directory, but the current change types can't overlap
    (only a real file's own commits carry is_unpushed_commit).
    """
    if change.is_unpushed_commit:
        return None, (
            "This is an already-committed (not yet pushed) change, so the file "
            "on disk no longer matches this diff."
        )
    if change.change_type == ChangeType.DELETED:
        return None, "The file no longer exists."
    if change.is_directory:
        return None, "This is a folder, not a file."
    return repo_path / change.path, None


def apply_tooltip_font_size(size: int) -> None:
    app = QApplication.instance()
    if app is None:
        return
    app.setStyleSheet(f"QToolTip {{ font-size: {size}pt; }}" if size > 0 else "")


def github_log(message: str) -> None:
    applog.log(f"GitHub: {message}", level=applog.LogLevel.INFO)


def find_repository(workspace: Workspace | None, repo_path: Path) -> Repository | None:
    if workspace is None:
        return None
    return next((r for r in workspace.repositories if r.path == repo_path), None)


def find_owning_repository(workspace: Workspace | None, folder_path: Path) -> Repository | None:
    # A folder can sit inside more than one repo's path when a nested repo
    # (e.g. a worktree) lives under its parent -- the deepest (longest
    # path) match is the one that actually owns the folder's files, so a
    # nested repo's own changes never get attributed to its parent's patch.
    if workspace is None:
        return None
    candidates = [r for r in workspace.repositories if folder_path.is_relative_to(r.path)]
    if not candidates:
        return None
    return max(candidates, key=lambda r: len(r.path.parts))
