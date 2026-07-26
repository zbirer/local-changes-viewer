from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QStandardItem, QStandardItemModel

from local_changes_viewer.core.domain.file_change import ChangeType
from local_changes_viewer.core.domain.workspace import Workspace

_CHANGE_COLORS = {
    ChangeType.MODIFIED: QColor("#3B82F6"),
    ChangeType.ADDED: QColor("#22C55E"),
    ChangeType.DELETED: QColor("#EF4444"),
    ChangeType.RENAMED: QColor("#A855F7"),
    ChangeType.UNTRACKED: QColor("#6B7280"),
    ChangeType.IGNORED: QColor("#9CA3AF"),
}

NODE_KEY_ROLE = Qt.ItemDataRole.UserRole + 1
FILE_CHANGE_ROLE = Qt.ItemDataRole.UserRole + 2
REPO_PATH_ROLE = Qt.ItemDataRole.UserRole + 3
_CHANGE_SIGNATURE_ROLE = Qt.ItemDataRole.UserRole + 4

_REFRESH_HIGHLIGHT_COLOR = QColor("#FEF3C7")
_REFRESH_HIGHLIGHT_TEXT_COLOR = QColor("#1F2937")


class RepoTreeModel(QStandardItemModel):
    def __init__(self) -> None:
        super().__init__()
        self.setHorizontalHeaderLabels(["Name"])

    def set_workspace(self, workspace: Workspace) -> None:
        self.clear()
        self.setHorizontalHeaderLabels(["Name"])
        root = self.invisibleRootItem()
        for repo in workspace.repositories:
            repo_item = self._build_repo_item(repo)
            root.appendRow(repo_item)
            self._add_changes(repo_item, repo)

    def update_workspace(self, workspace: Workspace) -> None:
        root = self.invisibleRootItem()
        existing_by_key: dict[str, QStandardItem] = {}
        for row in range(root.rowCount()):
            item = root.child(row)
            key = item.data(NODE_KEY_ROLE)
            if key is not None:
                existing_by_key[key] = item

        new_keys = {str(repo.path) for repo in workspace.repositories}
        for row in reversed(range(root.rowCount())):
            item = root.child(row)
            key = item.data(NODE_KEY_ROLE)
            if key is not None and key not in new_keys:
                root.removeRow(row)

        for repo in workspace.repositories:
            key = str(repo.path)
            existing_item = existing_by_key.get(key)
            signature = self._change_signature(repo)

            if existing_item is None:
                repo_item = self._build_repo_item(repo)
                root.appendRow(repo_item)
                self._add_changes(repo_item, repo)
                continue

            self._update_repo_item(existing_item, repo)
            if existing_item.data(_CHANGE_SIGNATURE_ROLE) != signature:
                existing_item.removeRows(0, existing_item.rowCount())
                self._add_changes(existing_item, repo)
                existing_item.setData(signature, _CHANGE_SIGNATURE_ROLE)

    def set_repo_highlighted(self, repo_path: Path, highlighted: bool) -> None:
        root = self.invisibleRootItem()
        key = str(repo_path)
        for row in range(root.rowCount()):
            item = root.child(row)
            if item.data(NODE_KEY_ROLE) == key:
                self._set_item_highlighted(item, highlighted)
                return

    def clear_all_highlights(self) -> None:
        root = self.invisibleRootItem()
        for row in range(root.rowCount()):
            self._set_item_highlighted(root.child(row), False)

    @staticmethod
    def _set_item_highlighted(item: QStandardItem, highlighted: bool) -> None:
        item.setBackground(QBrush(_REFRESH_HIGHLIGHT_COLOR) if highlighted else QBrush())
        item.setForeground(QBrush(_REFRESH_HIGHLIGHT_TEXT_COLOR) if highlighted else QBrush())

    def _build_repo_item(self, repo) -> QStandardItem:
        repo_item = QStandardItem("")
        repo_item.setEditable(False)
        repo_item.setData(str(repo.path), NODE_KEY_ROLE)
        repo_item.setData(self._change_signature(repo), _CHANGE_SIGNATURE_ROLE)
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
            (str(c.path), c.change_type, c.is_directory, str(c.old_path) if c.old_path else None)
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

        return tooltip

    @staticmethod
    def _add_changes(repo_item: QStandardItem, repo) -> None:
        changes = repo.changes
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
                    parent_item.appendRow(dir_item)
                    dir_items[accumulated] = dir_item
                parent_item = dir_item

            file_name = parts[-1] if parts else str(change.path)
            if change.is_directory:
                file_name += "/"
            file_item = QStandardItem(file_name)
            file_item.setEditable(False)
            file_item.setForeground(QBrush(_CHANGE_COLORS[change.change_type]))
            file_item.setData(change, FILE_CHANGE_ROLE)
            file_item.setData(str(repo.path), REPO_PATH_ROLE)
            parent_item.appendRow(file_item)
