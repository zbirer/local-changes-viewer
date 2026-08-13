from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel

from local_changes_viewer.core.domain.file_change import ChangeType
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.gui import applog

_CHANGE_COLORS = {
    ChangeType.MODIFIED: QColor("#3B82F6"),
    ChangeType.ADDED: QColor("#22C55E"),
    ChangeType.DELETED: QColor("#EF4444"),
    ChangeType.RENAMED: QColor("#A855F7"),
    ChangeType.UNTRACKED: QColor("#6B7280"),
    ChangeType.IGNORED: QColor("#9CA3AF"),
}

_UNPUSHED_COMMIT_COLOR = QColor("#F59E0B")

NODE_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
FILE_CHANGE_ROLE = Qt.ItemDataRole.UserRole + 2
REPO_PATH_ROLE = Qt.ItemDataRole.UserRole + 3
_CHANGE_SIGNATURE_ROLE = Qt.ItemDataRole.UserRole + 4
FOLDER_PATH_ROLE = Qt.ItemDataRole.UserRole + 5
_NODE_KIND_ROLE = Qt.ItemDataRole.UserRole + 6
_NODE_KIND_REPO = "repo"

_REFRESH_HIGHLIGHT_COLOR = QColor("#FEF3C7")
_REFRESH_HIGHLIGHT_TEXT_COLOR = QColor("#1F2937")

_REPO_BG_COLOR = QColor("#334155")
_REPO_TEXT_COLOR = QColor("#F9FAFB")


class RepoTreeModel(QStandardItemModel):
    def __init__(self) -> None:
        super().__init__()
        self.setHorizontalHeaderLabels(["Name"])

    def set_workspace(self, workspace: Workspace) -> None:
        self.clear()
        self.setHorizontalHeaderLabels(["Name"])
        root = self.invisibleRootItem()
        roots, children_by_parent = self._partition(workspace.repositories)
        self._sync_level(root, roots, children_by_parent)

    def update_workspace(self, workspace: Workspace) -> None:
        root = self.invisibleRootItem()
        roots, children_by_parent = self._partition(workspace.repositories)
        self._sync_level(root, roots, children_by_parent)

    def has_rows(self) -> bool:
        return self.invisibleRootItem().rowCount() > 0

    def _sync_level(
        self,
        container_item: QStandardItem,
        repos: list,
        children_by_parent: dict,
    ) -> None:
        # A container passed here may be a repo_item that ALSO holds unrelated
        # children -- its own change/dir rows (added by _add_changes) and, for
        # a directly-nested repo (e.g. a git worktree discovered via a logical
        # parent, which has no filesystem-relative intermediate directory),
        # sibling "nested-dir::" container items built by _sync_nested_repos
        # for OTHER nested repos. Those rows are keyed too, but with formats
        # that never match a repo path, so without this kind filter every one
        # of them looked "stale" here and got removeRow'd out from under a
        # reference already captured (and about to be recursed into) by
        # _sync_nested_repos -- the exact crash in this file's history:
        # RuntimeError: libshiboken: Internal C++ object (QStandardItem)
        # already deleted, raised from the next level's _sync_level call.
        existing_by_key: dict[str, QStandardItem] = {}
        for row in range(container_item.rowCount()):
            item = container_item.child(row)
            if item.data(_NODE_KIND_ROLE) != _NODE_KIND_REPO:
                continue
            key = item.data(NODE_KEY_ROLE)
            if key is not None:
                existing_by_key[key] = item

        new_keys = {str(repo.path) for repo in repos}
        for row in reversed(range(container_item.rowCount())):
            item = container_item.child(row)
            if item.data(_NODE_KIND_ROLE) != _NODE_KIND_REPO:
                continue
            key = item.data(NODE_KEY_ROLE)
            if key is not None and key not in new_keys:
                container_item.removeRow(row)

        for repo in repos:
            key = str(repo.path)
            existing_item = existing_by_key.get(key)
            nested = children_by_parent.get(key, [])
            signature = self._change_signature(repo)

            if existing_item is None:
                repo_item = self._build_repo_item(repo)
                container_item.appendRow(repo_item)
                self._add_changes(repo_item, repo, self._blocked_dirs(repo, nested))
                repo_item.setData(signature, _CHANGE_SIGNATURE_ROLE)
            else:
                repo_item = existing_item
                self._update_repo_item(repo_item, repo)
                if existing_item.data(_CHANGE_SIGNATURE_ROLE) != signature:
                    existing_item.removeRows(0, existing_item.rowCount())
                    self._add_changes(repo_item, repo, self._blocked_dirs(repo, nested))
                    existing_item.setData(signature, _CHANGE_SIGNATURE_ROLE)

            self._sync_nested_repos(repo_item, repo, nested, children_by_parent)

    def _sync_nested_repos(
        self,
        repo_item: QStandardItem,
        repo,
        nested: list,
        children_by_parent: dict,
    ) -> None:
        # Nested repos (worktrees included) render unconditionally here, the
        # same way top-level roots do in _sync_level -- WorktreesDialog lists
        # every worktree straight from `git worktree list` regardless of its
        # dirty state, and this tree must match that, not silently hide a
        # clean worktree the user right-clicked to find. Hiding repos with no
        # changes is exclusively the job of the opt-in, descendant-aware
        # "Hide repos without changes" setting (F35), applied earlier in
        # workspace_filter.filter_workspace before a Workspace ever reaches
        # this model -- filtering it again here unconditionally, independent
        # of that setting, is exactly the bug this comment replaces: a nested
        # worktree with no uncommitted changes never appeared in the tree
        # even with the setting off (its default), even though the same
        # worktree was correctly listed by WorktreesDialog.
        dir_items: dict[Path, QStandardItem] = {}
        children_by_container: dict[int, tuple[QStandardItem, list]] = {}
        live_dir_keys: set[str] = set()

        for child in nested:
            parent_item = repo_item
            accumulated = Path()
            for part in self._relative_parts(child, repo)[:-1]:
                accumulated = accumulated / part
                dir_key = f"nested-dir::{repo.path}::{accumulated}"
                live_dir_keys.add(dir_key)
                dir_item = dir_items.get(accumulated)
                if dir_item is None:
                    dir_item = self._find_child_by_key(parent_item, dir_key)
                    if dir_item is None:
                        dir_item = QStandardItem(part)
                        dir_item.setEditable(False)
                        dir_item.setData(dir_key, NODE_KEY_ROLE)
                        dir_item.setData(str(repo.path / accumulated), FOLDER_PATH_ROLE)
                        parent_item.appendRow(dir_item)
                    dir_items[accumulated] = dir_item
                parent_item = dir_item

            entry = children_by_container.setdefault(id(parent_item), (parent_item, []))
            entry[1].append(child)

        self._prune_stale_dirs(repo_item, live_dir_keys)

        for container_item, children in children_by_container.values():
            self._sync_level(container_item, children, children_by_parent)

    @staticmethod
    def _prune_stale_dirs(item: QStandardItem, live_dir_keys: set[str]) -> None:
        # Synthetic directory nodes (created above for intermediate path segments
        # between a repo and a nested repo) have no counterpart in _sync_level's
        # removal pass, so a directory whose nested repo got filtered out (or
        # removed) would otherwise linger in the tree forever.
        for row in reversed(range(item.rowCount())):
            child = item.child(row)
            key = child.data(NODE_KEY_ROLE)
            if key is not None and str(key).startswith("nested-dir::"):
                if key not in live_dir_keys:
                    applog.log(
                        f"Pruning stale nested-repo dir node {key!r} ({child.rowCount()} children)",
                        level=applog.LogLevel.DEBUG,
                    )
                    item.removeRow(row)
                else:
                    RepoTreeModel._prune_stale_dirs(child, live_dir_keys)

    @staticmethod
    def _find_child_by_key(parent_item: QStandardItem, key: str) -> QStandardItem | None:
        for row in range(parent_item.rowCount()):
            child = parent_item.child(row)
            if child.data(NODE_KEY_ROLE) == key:
                return child
        return None

    @staticmethod
    def _relative_parts(child, repo) -> tuple:
        # A nested repo discovered via a logical parent (e.g. a git worktree
        # living in a sibling directory) isn't a filesystem subpath of its
        # parent's path, so relative_to would raise; treat it as a direct
        # child in that case instead of an intermediate-directory descendant.
        try:
            return child.path.relative_to(repo.path).parts
        except ValueError:
            return (child.path.name,)

    @staticmethod
    def _blocked_dirs(repo, nested: list) -> set:
        blocked: set = set()
        for child in nested:
            accumulated = Path()
            for part in RepoTreeModel._relative_parts(child, repo)[:-1]:
                accumulated = accumulated / part
                blocked.add(accumulated)
        return blocked

    @staticmethod
    def _partition(repositories: list) -> tuple[list, dict[str, list]]:
        # De-duplicate by path up front, keeping the first-seen occurrence.
        # Without this, two Repository objects sharing a path would each be
        # inferred as the other's parent below (a path is trivially
        # relative_to itself), so both would drop out of `roots` and the
        # tree would render empty. `by_path` is derived from this same
        # de-duplicated list so the two never disagree.
        deduped: list = []
        seen_paths: set[str] = set()
        for repo in repositories:
            path_key = str(repo.path)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            deduped.append(repo)
        repositories = deduped
        by_path = {str(r.path): r for r in repositories}
        parent_of: dict[str, str | None] = {}
        for repo in repositories:
            logical_parent = getattr(repo, "logical_parent_path", None)
            if logical_parent is not None and str(logical_parent) in by_path:
                parent_of[str(repo.path)] = str(logical_parent)
                continue

            best_parent: str | None = None
            for other in repositories:
                if other is repo or other.path == repo.path:
                    continue
                try:
                    repo.path.relative_to(other.path)
                except ValueError:
                    continue
                if best_parent is None or len(other.path.parts) > len(
                    by_path[best_parent].path.parts
                ):
                    best_parent = str(other.path)
            parent_of[str(repo.path)] = best_parent

        roots = [r for r in repositories if parent_of[str(r.path)] is None]
        children_by_parent: dict[str, list] = {}
        for repo in repositories:
            parent = parent_of[str(repo.path)]
            if parent is not None:
                children_by_parent.setdefault(parent, []).append(repo)
        return roots, children_by_parent

    def set_repo_highlighted(self, repo_path: Path, highlighted: bool) -> None:
        key = str(repo_path)
        item = self._find_item_by_key(self.invisibleRootItem(), key)
        if item is not None:
            self._set_item_highlighted(item, highlighted)

    @staticmethod
    def _find_item_by_key(parent_item: QStandardItem, key: str) -> QStandardItem | None:
        for row in range(parent_item.rowCount()):
            item = parent_item.child(row)
            if item.data(NODE_KEY_ROLE) == key:
                return item
            found = RepoTreeModel._find_item_by_key(item, key)
            if found is not None:
                return found
        return None

    def clear_all_highlights(self) -> None:
        root = self.invisibleRootItem()
        for row in range(root.rowCount()):
            self._set_item_highlighted(root.child(row), False)

    @staticmethod
    def _set_item_highlighted(item: QStandardItem, highlighted: bool) -> None:
        item.setBackground(
            QBrush(_REFRESH_HIGHLIGHT_COLOR) if highlighted else QBrush(_REPO_BG_COLOR)
        )
        item.setForeground(
            QBrush(_REFRESH_HIGHLIGHT_TEXT_COLOR) if highlighted else QBrush(_REPO_TEXT_COLOR)
        )

    def _build_repo_item(self, repo) -> QStandardItem:
        repo_item = QStandardItem("")
        repo_item.setEditable(False)
        repo_item.setData(str(repo.path), NODE_KEY_ROLE)
        repo_item.setData(_NODE_KIND_REPO, _NODE_KIND_ROLE)
        repo_item.setData(str(repo.path), FOLDER_PATH_ROLE)
        repo_item.setData(self._change_signature(repo), _CHANGE_SIGNATURE_ROLE)
        repo_item.setBackground(QBrush(_REPO_BG_COLOR))
        repo_item.setForeground(QBrush(_REPO_TEXT_COLOR))
        self._update_repo_item(repo_item, repo)
        return repo_item

    @staticmethod
    def _update_repo_item(repo_item: QStandardItem, repo) -> None:
        branch = repo.branch_status
        label = (
            f"{repo.name}  [{branch.branch_name}, +{branch.ahead}/-{branch.behind}]"
            f"  ({len(repo.changes)})"
        )
        if repo.pull_request is not None:
            label += f"  [PR #{repo.pull_request.number} {repo.pull_request.state}]"
        if repo_item.text() != label:
            repo_item.setText(label)
        repo_item.setToolTip(RepoTreeModel._repo_tooltip(repo))

    @staticmethod
    def _change_signature(repo) -> tuple:
        return tuple(
            (
                str(c.path),
                c.change_type,
                c.is_directory,
                str(c.old_path) if c.old_path else None,
                c.is_unpushed_commit,
            )
            for c in repo.changes
        )

    @staticmethod
    def _repo_tooltip(repo) -> str:
        branch = repo.branch_status
        change_count = len(repo.changes)
        change_text = "1 changed file" if change_count == 1 else f"{change_count} changed files"

        def _commit_word(count: int) -> str:
            return "commit" if count == 1 else "commits"

        ahead_behind_parts = []
        if branch.ahead:
            ahead_behind_parts.append(
                f"local is ahead of origin by {branch.ahead} {_commit_word(branch.ahead)}"
            )
        if branch.behind:
            ahead_behind_parts.append(
                f"local is behind origin by {branch.behind} {_commit_word(branch.behind)}"
            )
        ahead_behind_text = ", ".join(ahead_behind_parts) if ahead_behind_parts else "up to date"

        tooltip = (
            f"Name: {repo.name}\n"
            f"Status: {change_text}, {ahead_behind_text}\n"
            f"Branch: {branch.branch_name}\n"
            f"Parent of branch: {branch.parent_branch or '(none)'}\n"
            f"Default branch: {branch.default_branch or '(none)'}"
        )

        pr = repo.pull_request
        if pr is not None:
            total_comments = pr.comment_count + pr.review_comment_count
            comment_text = "1 comment" if total_comments == 1 else f"{total_comments} comments"
            tooltip += (
                f"\nPull request: #{pr.number} {pr.title} ({pr.state}, {comment_text})"
            )

        tooltip += f"\nPath: {str(repo.path)}"

        return tooltip

    @staticmethod
    def _add_changes(repo_item: QStandardItem, repo, skip_dirs: set | None = None) -> None:
        skip_dirs = skip_dirs or set()
        changes = [
            change
            for change in repo.changes
            if not (change.is_directory and change.path in skip_dirs)
        ]
        dir_items: dict[Path, QStandardItem] = {}
        dir_counts: dict[Path, int] = {}
        for change in changes:
            parts = change.path.parts
            accumulated = Path()
            for part in parts[:-1]:
                accumulated = accumulated / part
                dir_counts[accumulated] = dir_counts.get(accumulated, 0) + 1

        for change in changes:
            parts = change.path.parts
            parent_item = repo_item
            accumulated = Path()
            for part in parts[:-1]:
                accumulated = accumulated / part
                dir_item = dir_items.get(accumulated)
                if dir_item is None:
                    dir_item = QStandardItem(f"{part}  ({dir_counts[accumulated]})")
                    dir_item.setEditable(False)
                    dir_item.setData(f"{repo.path}::{accumulated}", NODE_KEY_ROLE)
                    dir_item.setData(str(repo.path / accumulated), FOLDER_PATH_ROLE)
                    parent_item.appendRow(dir_item)
                    dir_items[accumulated] = dir_item
                parent_item = dir_item

            file_name = parts[-1] if parts else str(change.path)
            if change.is_directory:
                file_name += "/"
            if change.is_unpushed_commit:
                file_name += "  (unpushed commit)"
            file_item = QStandardItem(file_name)
            file_item.setEditable(False)
            color = (
                _UNPUSHED_COMMIT_COLOR
                if change.is_unpushed_commit
                else _CHANGE_COLORS[change.change_type]
            )
            file_item.setForeground(QBrush(color))
            if change.is_unpushed_commit and change.commit_message:
                file_item.setToolTip(change.commit_message)
            file_item.setData(change, FILE_CHANGE_ROLE)
            file_item.setData(str(repo.path), REPO_PATH_ROLE)
            parent_item.appendRow(file_item)

        applog.log(
            f"_add_changes for {repo.path}: {len(changes)} changes -> "
            f"{repo_item.rowCount()} top-level rows",
            level=applog.LogLevel.VERBOSE,
        )
