"""The "Show stashes..." repo-root context-menu action's dialog: lists a
repo's `git stash` entries, and for the selected one, shows the list of
files it changed with each file's diff rendered in the app's own
side-by-side view -- not raw patch text -- so a stash's diff reads exactly
like every other diff in this app.

Loading `list_stashes()` is a single, fast `git stash list` call -- unlike
`WorktreesDialog`'s per-worktree detail gathering (several git commands each),
there's no need for `worktrees_dialog.py`'s background-worker/busy-dialog
machinery here; the list is populated synchronously in `__init__`.

Constructor shape (`repo_path` + `adapter_factory`) deliberately mirrors
`WorktreesDialog`, so this dialog can be constructed in a test with a fake
adapter_factory and no real git repo on disk, exactly the same way.

Splitting the stash's raw diff text into one chunk per file, and parsing a
single file's chunk into the `DiffResult` the side-by-side view consumes,
are both owned by `PatchService`/`GitRepoAdapter` respectively -- this
dialog only wires the two together (stash ref -> file list -> selected
file's `DiffResult`), the same "diff --git boundary walking lives in
exactly one place" rule `PatchService.split_patch` exists to enforce.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import git

from local_changes_viewer.core.domain.file_change import ChangeType, PatchFileDiff
from local_changes_viewer.core.domain.stash_entry import StashEntry
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.core.services.patch_service import PatchService
from local_changes_viewer.gui.diff_view.side_by_side_view import SideBySideView

_COLUMNS = ("Ref", "Message", "Date")
_STASH_ROLE = Qt.ItemDataRole.UserRole
_FILE_PATH_ROLE = Qt.ItemDataRole.UserRole

# Mirrors PatchFileSelectionDialog's row labels -- kept as its own small map
# here (rather than importing that dialog's) since this dialog only needs the
# label text, and the two dialogs' widgets are otherwise unrelated.
_CHANGE_TYPE_LABELS = {
    ChangeType.MODIFIED: "M",
    ChangeType.ADDED: "A",
    ChangeType.DELETED: "D",
    ChangeType.RENAMED: "R",
    ChangeType.UNTRACKED: "U",
    ChangeType.IGNORED: "I",
}


class StashesDialog(QDialog):
    def __init__(
        self,
        repo_path: Path,
        adapter_factory=GitRepoAdapter,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo_path = repo_path
        self._adapter_factory = adapter_factory
        self._patch_service = PatchService()
        # The selected stash's files, in the shape `_on_file_selection_changed`
        # looks a clicked row's path up in -- repopulated by
        # `_on_stash_selection_changed`, emptied by `_clear_file_list_and_diff`.
        self._file_diffs: list[PatchFileDiff] = []
        self._current_stash: StashEntry | None = None
        # Told to the caller (main_window's _on_show_stashes_for_repo) so it
        # knows whether to refresh the repo after this dialog closes -- an
        # Apply/Pop restore changes the working tree, so the tree/diff panes
        # showing this repo are stale otherwise, same idea as WorktreesDialog's
        # `deleted_any`.
        self.restored = False
        self.setWindowTitle(f"Stashes — {repo_path.name}")
        parent_width = parent.width() if parent is not None else 1100
        parent_height = parent.height() if parent is not None else 700
        # Wide enough that the side-by-side view (its right-hand, 3x-stretch
        # pane below) is actually usable rather than a sliver -- a plain
        # patch-text pane didn't need the width this feature does.
        self.resize(max(int(parent_width * 0.85), 1000), max(int(parent_height * 0.85), 650))

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.itemSelectionChanged.connect(self._on_stash_selection_changed)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)

        self._file_list = QListWidget()
        self._file_list.setAlternatingRowColors(True)
        self._file_list.currentItemChanged.connect(self._on_file_selection_changed)
        self._file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._file_list.customContextMenuRequested.connect(self._on_file_list_context_menu)

        self._side_by_side = SideBySideView()
        # A stash diff describes the stashed state, not the file currently on
        # disk -- editing against a mismatched worktree file would silently
        # corrupt it, so edit mode must never be offered here (see
        # `set_file_target`'s None handling: it's a plain attribute reset,
        # nothing dereferences it, so this is safe on its own).
        self._side_by_side.set_file_target(None)

        # Shown instead of `_diff_splitter` when the selected stash's diff
        # couldn't be loaded, or touched no files -- see `_show_file_status`.
        self._file_status_label = QLabel("")
        self._file_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._file_status_label.setWordWrap(True)
        self._file_status_label.setVisible(False)

        self._empty_label = QLabel("No stashes in this repository.")
        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_label.setVisible(False)

        self._diff_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._diff_splitter.addWidget(self._file_list)
        self._diff_splitter.addWidget(self._side_by_side)
        self._diff_splitter.setStretchFactor(0, 1)
        self._diff_splitter.setStretchFactor(1, 3)

        diff_area = QWidget()
        diff_area_layout = QVBoxLayout(diff_area)
        diff_area_layout.setContentsMargins(0, 0, 0, 0)
        diff_area_layout.addWidget(self._file_status_label)
        diff_area_layout.addWidget(self._diff_splitter)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._table)
        splitter.addWidget(diff_area)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        self._apply_button = QPushButton("Apply")
        self._pop_button = QPushButton("Pop")
        self._apply_button.clicked.connect(self._on_apply)
        self._pop_button.clicked.connect(self._on_pop)

        button_row = QHBoxLayout()
        button_row.addWidget(self._apply_button)
        button_row.addWidget(self._pop_button)
        button_row.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(self._empty_label)
        layout.addWidget(splitter)
        layout.addLayout(button_row)

        self._reload()

    def _reload(self) -> None:
        adapter = self._adapter_factory(self._repo_path)
        try:
            stashes = adapter.list_stashes()
        except git.GitCommandError as error:
            QMessageBox.warning(self, "Show Stashes", f"Failed to list stashes: {error}")
            stashes = []
        self._populate_table(stashes)

    def _populate_table(self, stashes: list[StashEntry]) -> None:
        self._table.setRowCount(len(stashes))
        for row, entry in enumerate(stashes):
            date_text = entry.created_at.strftime("%Y-%m-%d %H:%M") if entry.created_at else "—"
            values = (entry.ref, entry.message, date_text)
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(_STASH_ROLE, entry)
                self._table.setItem(row, column, item)
        self._table.resizeColumnsToContents()

        is_empty = not stashes
        self._empty_label.setVisible(is_empty)
        self._table.setVisible(not is_empty)
        self._current_stash = None
        self._clear_file_list_and_diff()
        self._update_button_state()

    def _stash_at_row(self, row: int) -> StashEntry | None:
        item = self._table.item(row, 0)
        if item is None:
            return None
        return item.data(_STASH_ROLE)

    def _selected_stash(self) -> StashEntry | None:
        rows = self._table.selectionModel().selectedRows() if self._table.selectionModel() else []
        if not rows:
            return None
        return self._stash_at_row(rows[0].row())

    def _update_button_state(self) -> None:
        has_selection = self._selected_stash() is not None
        self._apply_button.setEnabled(has_selection)
        self._pop_button.setEnabled(has_selection)

    def _clear_file_list_and_diff(self) -> None:
        self._file_diffs = []
        self._file_list.clear()
        self._side_by_side.clear_diff()
        self._side_by_side.set_file_target(None)
        self._show_file_status(None)

    def _show_file_status(self, message: str | None) -> None:
        """Toggles between the file-list/side-by-side splitter and a plain
        message label -- used for "no files" and "failed to load" outcomes,
        so neither ever renders as a blank pane.
        """
        has_message = message is not None
        self._file_status_label.setText(message or "")
        self._file_status_label.setVisible(has_message)
        self._diff_splitter.setVisible(not has_message)

    def _on_stash_selection_changed(self) -> None:
        self._update_button_state()
        stash = self._selected_stash()
        self._current_stash = stash
        self._clear_file_list_and_diff()
        if stash is None:
            return
        adapter = self._adapter_factory(self._repo_path)
        try:
            patch_text = adapter.stash_diff(stash.ref)
        except git.GitCommandError as error:
            self._show_file_status(f"Failed to load diff: {error}")
            return

        self._file_diffs = self._patch_service.split_patch(patch_text)
        if not self._file_diffs:
            self._show_file_status("This stash didn't change any files.")
            return

        for diff in self._file_diffs:
            label = _CHANGE_TYPE_LABELS.get(diff.change_type, "?")
            item = QListWidgetItem(f"[{label}] {diff.path.as_posix()}")
            item.setData(_FILE_PATH_ROLE, diff.path)
            self._file_list.addItem(item)
        # Auto-select the first file so the pane is never blank on a fresh
        # stash selection -- this fires `_on_file_selection_changed` itself.
        self._file_list.setCurrentRow(0)

    def _on_file_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        path = current.data(_FILE_PATH_ROLE)
        diff_entry = next((d for d in self._file_diffs if d.path == path), None)
        if diff_entry is None:
            return
        stash_ref = self._current_stash.ref if self._current_stash is not None else "stash"
        result = GitRepoAdapter.parse_unified_diff(
            diff_entry.diff_text, old_ref="HEAD", new_ref=stash_ref
        )
        self._side_by_side.set_diff(result, str(diff_entry.path))
        self._side_by_side.set_file_target(None)

    def _on_apply(self) -> None:
        stash = self._selected_stash()
        if stash is None:
            return
        adapter = self._adapter_factory(self._repo_path)
        try:
            adapter.apply_stash(stash.ref)
        except git.GitCommandError as error:
            QMessageBox.critical(self, "Apply Stash", f"Failed to apply stash: {error}")
            return
        self.restored = True
        QMessageBox.information(self, "Apply Stash", f"Applied {stash.ref}.")
        self._reload()

    def _on_pop(self) -> None:
        stash = self._selected_stash()
        if stash is None:
            return
        confirm = QMessageBox.question(
            self,
            "Pop Stash",
            f"Restore {stash.ref} ({stash.message}) and delete this stash entry?\n\n"
            "This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        adapter = self._adapter_factory(self._repo_path)
        try:
            adapter.pop_stash(stash.ref)
        except git.GitCommandError as error:
            QMessageBox.critical(self, "Pop Stash", f"Failed to pop stash: {error}")
            return
        self.restored = True
        QMessageBox.information(self, "Pop Stash", f"Popped {stash.ref}.")
        self._reload()

    def _on_table_context_menu(self, pos) -> None:
        # Mirrors main_window.py's _on_tree_context_menu: right-click on a row
        # first makes that row current/selected, so the menu's actions (and
        # _selected_stash(), which they rely on) act on the row under the
        # cursor rather than whatever was selected before -- and empty space
        # (invalid index) shows no menu at all.
        index = self._table.indexAt(pos)
        if not index.isValid():
            return
        self._table.selectRow(index.row())
        stash = self._stash_at_row(index.row())
        if stash is None:
            return
        menu = QMenu(self._table)
        # "Restore stash" reuses _on_apply verbatim -- same handler, same
        # confirmation-free apply + refresh path as the Apply button, so
        # there's exactly one place that logic lives.
        menu.addAction("Restore stash", self._on_apply)
        menu.addAction("Delete stash", lambda: self._on_delete_stash(stash))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _on_delete_stash(self, stash: StashEntry) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete Stash",
            f"Delete {stash.ref} ({stash.message})?\n\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        adapter = self._adapter_factory(self._repo_path)
        try:
            adapter.drop_stash(stash.ref)
        except git.GitCommandError as error:
            QMessageBox.critical(self, "Delete Stash", f"Failed to delete stash: {error}")
            return
        QMessageBox.information(self, "Delete Stash", f"Deleted {stash.ref}.")
        # `git stash drop` renumbers every remaining stash -- dropping
        # stash@{1} turns stash@{2} into stash@{1} -- so the table must be
        # fully reloaded from git, not just have this one row removed;
        # _reload -> _populate_table also clears _current_stash and the
        # file list/diff pane, which is correct even when the dropped entry
        # wasn't the selected one.
        self._reload()

    def _on_file_list_context_menu(self, pos) -> None:
        item = self._file_list.itemAt(pos)
        if item is None:
            return
        self._file_list.setCurrentItem(item)
        path = item.data(_FILE_PATH_ROLE)
        menu = QMenu(self._file_list)
        menu.addAction("Restore file", lambda: self._on_restore_file(path))
        menu.exec(self._file_list.viewport().mapToGlobal(pos))

    def _on_restore_file(self, path: Path) -> None:
        stash = self._current_stash
        if stash is None:
            return
        confirm = QMessageBox.question(
            self,
            "Restore File",
            f"Restore {path.as_posix()} from {stash.ref}?\n\n"
            "This will overwrite the working-tree copy of this file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        adapter = self._adapter_factory(self._repo_path)
        try:
            adapter.restore_file_from_stash(stash.ref, path)
        except git.GitCommandError as error:
            QMessageBox.critical(self, "Restore File", f"Failed to restore file: {error}")
            return
        self.restored = True
        QMessageBox.information(self, "Restore File", f"Restored {path.as_posix()}.")
