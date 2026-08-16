"""File-selection step for the "Create patch" feature (see main_window.py's
`_create_patch_with_selection`): lists every modified file `PatchService.
files_in_scope()` found under the right-clicked target, each with a checkbox,
all checked by default, so the user can narrow a folder- or repo-scoped patch
down to just the files they actually want before it's built. Shown even when
the target is a single file (it then lists one checked row) rather than
special-cased away -- unchecking that one row and hitting OK is a legitimate
way to abort without reaching for Cancel, and a single code path is one less
place for "the menu item silently does nothing" bugs (this repo's own
history) to hide.

Rows come pre-sorted from `files_in_scope()` (path order) -- this dialog
displays them as given rather than re-sorting, so the sort stays owned by the
one place that knows the whole scope-resolution story.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange

# Mirrors the labels the tree already shows for each ChangeType -- kept as a
# small local map rather than importing tree_model's mapping, since this
# dialog only needs the label text, not the icon/color machinery built for a
# live, repaint-on-every-scan tree view.
_CHANGE_TYPE_LABELS = {
    ChangeType.MODIFIED: "Modified",
    ChangeType.ADDED: "Added",
    ChangeType.DELETED: "Deleted",
    ChangeType.RENAMED: "Renamed",
    ChangeType.UNTRACKED: "Untracked",
    ChangeType.IGNORED: "Ignored",
}

_PATH_ROLE = Qt.ItemDataRole.UserRole


class PatchFileSelectionDialog(QDialog):
    def __init__(self, changes: list[FileChange], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Patch — Select Files")
        parent_width = parent.width() if parent is not None else 640
        self.resize(int(parent_width * 0.6), 420)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        for change in changes:
            label = _CHANGE_TYPE_LABELS.get(change.change_type, "Changed")
            item = QListWidgetItem(f"[{label}]  {change.path.as_posix()}")
            item.setData(_PATH_ROLE, change.path)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        # Connected only after every row is populated above -- each addItem()
        # would otherwise itself be a change event, running the OK-button
        # recompute once per row for no reason (the final _update_ok_enabled()
        # call below already covers the initial "all checked" state).
        self._list.itemChanged.connect(self._on_item_changed)

        self._select_all_button = QPushButton("Select All")
        self._deselect_all_button = QPushButton("Deselect All")
        self._select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self._deselect_all_button.clicked.connect(lambda: self._set_all_checked(False))

        button_row = QHBoxLayout()
        button_row.addWidget(self._select_all_button)
        button_row.addWidget(self._deselect_all_button)
        button_row.addStretch(1)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(button_row)
        layout.addWidget(self._list)
        layout.addWidget(self._buttons)

        # An empty patch is never a useful outcome to reach through this
        # dialog, so OK starts (and stays) disabled the moment nothing is
        # checked -- rather than letting the user hit OK and then showing
        # them the same "nothing to patch" message _present_patch already
        # has to handle for the zero-scope case.
        self._update_ok_enabled()

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        # Blocked while flipping every row so itemChanged doesn't run the
        # O(n) OK-button recompute once per row -- _update_ok_enabled() below
        # still runs once, after every row has settled.
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)
        self._list.blockSignals(False)
        self._update_ok_enabled()

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        self._update_ok_enabled()

    def _update_ok_enabled(self) -> None:
        any_checked = any(
            self._list.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self._list.count())
        )
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(any_checked)

    def selected_paths(self) -> list[Path]:
        return [
            self._list.item(i).data(_PATH_ROLE)
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        ]
