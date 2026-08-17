from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.worktree_info import WorktreeInfo
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.gui.bulk_delete_worktrees_dialog import BulkDeleteWorktreesDialog
from local_changes_viewer.gui.worktree_changes_dialog import WorktreeChangesDialog
from local_changes_viewer.gui.workers.worktree_details_worker import WorktreeDetailsWorker

_COLUMNS = ("Path", "Branch", "Last Commit / Modified", "Unpushed Changes", "Created")
_WORKTREE_ROLE = Qt.ItemDataRole.UserRole


class WorktreesDialog(QDialog):
    def __init__(
        self,
        repo_path: Path,
        adapter_factory=GitRepoAdapter,
        parent: QWidget | None = None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo_path = repo_path
        self._adapter_factory = adapter_factory
        self._thread_pool = thread_pool if thread_pool is not None else QThreadPool.globalInstance()
        self._loading = False
        self.deleted_any = False
        self.setWindowTitle(f"Worktrees — {repo_path.name}")
        # Starting size before real data (and therefore real column widths)
        # exists; `_fit_dialog_width()` grows this once the table is
        # populated so every header title is fully visible.
        parent_width = parent.width() if parent is not None else 900
        self.resize(int(parent_width * 0.7), 400)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setHorizontalHeaderLabels(_COLUMNS)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        for column in range(len(_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        self._table.setSortingEnabled(True)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu_requested)
        self._table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        # itemSelectionChanged is the QTableWidget-level equivalent of
        # selectionModel().selectionChanged -- it fires for both a click and
        # a programmatic selectRow(), so the action buttons below track the
        # current selection however it changes.
        self._table.itemSelectionChanged.connect(self._update_action_buttons_enabled)

        self._row_worktrees: list[WorktreeInfo] = []

        # Lives inside this dialog's own layout, not a separate top-level
        # window: an earlier version showed a standalone modal "busy" dialog
        # instead, but `main_window.py` calls `exec()` on THIS dialog after
        # `_reload()` has already shown that other window, which raises this
        # dialog above it -- so the busy window ended up hidden behind the
        # (empty-looking) table and the user never saw it at all.
        self._status_label = QLabel("Reading worktree data …")
        layout = QVBoxLayout(self)
        layout.addWidget(self._status_label)
        layout.addWidget(self._table)

        # Same three actions as the right-click context menu, exposed as
        # buttons for users who don't discover (or prefer not to use) the
        # context menu. They share the exact same handlers -- reusing
        # `_worktree_at_row` via the current selection -- so there is one
        # source of truth for what "Delete" / "Show Changes" / "Copy Path"
        # actually do.
        self._delete_button = QPushButton("Delete")
        self._show_changes_button = QPushButton("Show Changes")
        self._copy_path_button = QPushButton("Copy Path")
        self._delete_button.clicked.connect(self._on_delete_button_clicked)
        self._show_changes_button.clicked.connect(self._on_show_changes_button_clicked)
        self._copy_path_button.clicked.connect(self._on_copy_path_button_clicked)
        # Unlike the three buttons above, this one is not row-scoped -- it
        # opens a picker over every loaded worktree rather than acting on the
        # current selection, so its enabled state is computed separately in
        # _update_action_buttons_enabled rather than gated on
        # _selected_worktree().
        self._bulk_delete_button = QPushButton("Delete Unmodified…")
        self._bulk_delete_button.clicked.connect(self._on_bulk_delete_button_clicked)
        button_row = QHBoxLayout()
        button_row.addWidget(self._delete_button)
        button_row.addWidget(self._show_changes_button)
        button_row.addWidget(self._copy_path_button)
        button_row.addWidget(self._bulk_delete_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self._status_label.hide()
        self._update_action_buttons_enabled()

        self._reload()

    def _reload(self) -> None:
        # list_worktree_details() runs several git commands per worktree; kept
        # off the GUI thread (see WorktreeDetailsWorker) so the app doesn't
        # freeze for the few seconds that takes with more than a couple of
        # worktrees. Also re-entered after a successful delete (_on_delete),
        # so it must stay safe to call more than once per dialog lifetime.
        # The table is disabled while loading: it still shows the previous
        # (stale) rows during a post-delete reload, and without this a user
        # could right-click and fire a second delete before the refreshed
        # data (and re-enable) comes back.
        self._loading = True
        self._status_label.show()
        self._table.setEnabled(False)
        self._update_action_buttons_enabled()

        worker = WorktreeDetailsWorker(self._repo_path, self._adapter_factory)
        worker.signals.finished.connect(self._on_worktree_details_ready)
        worker.signals.error.connect(self._on_worktree_details_error)
        self._thread_pool.start(worker)

    def _on_worktree_details_ready(self, details: list[WorktreeInfo]) -> None:
        self._finish_loading()
        self._populate_table(details)

    def _on_worktree_details_error(self, message: str) -> None:
        self._finish_loading()
        QMessageBox.warning(self, "List Worktrees failed", f"Failed to list worktrees: {message}")
        self._populate_table([])

    def _finish_loading(self) -> None:
        self._loading = False
        self._status_label.hide()
        self._table.setEnabled(True)
        self._update_action_buttons_enabled()

    def _populate_table(self, details: list[WorktreeInfo]) -> None:
        self._row_worktrees = list(details)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(details))
        for row, info in enumerate(details):
            values = (
                str(info.path),
                info.branch_name,
                info.last_activity.strftime("%Y-%m-%d %H:%M") if info.last_activity else "—",
                "Yes" if info.has_unpushed_changes else "No",
                info.created_at.strftime("%Y-%m-%d %H:%M") if info.created_at else "—",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(_WORKTREE_ROLE, info)
                self._table.setItem(row, column, item)

        if not details:
            self._table.setRowCount(1)
            placeholder = QTableWidgetItem("No linked worktrees")
            self._table.setItem(0, 0, placeholder)
            self._table.setSpan(0, 0, 1, len(_COLUMNS))

        self._table.setSortingEnabled(True)
        self._fit_dialog_width()
        # Row count/selection just changed (new data or the placeholder
        # row), so re-evaluate whether the action buttons should be enabled.
        self._update_action_buttons_enabled()

    def _fit_dialog_width(self) -> None:
        # Grows (never shrinks) the dialog so every column header -- e.g.
        # "Unpushed Changes" / "Created", which used to get clipped at the
        # old fixed 0.7 * parent-width -- is fully visible, capped at the
        # parent window's width (or the screen's, with no parent) as the
        # "100% of the app window" ceiling. Runs on every reload rather than
        # trying to distinguish "the user dragged this dialog smaller" from
        # any other resize (Qt gives no clean signal for that); because the
        # target is `max(current width, needed width)`, this never fights a
        # user who has *enlarged* the dialog, only one who has shrunk it.
        header = self._table.horizontalHeader()
        # ResizeToContents (unlike a plain resizeColumnsToContents(), which
        # only measures cell contents) is what makes Qt size each column to
        # fit the header label text too.
        for column in range(len(_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._table.resizeColumnsToContents()
        columns_width = sum(self._table.columnWidth(c) for c in range(len(_COLUMNS)))
        # Switch back to Interactive so the user can still drag column borders.
        for column in range(len(_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)

        frame_width = 2 * self._table.frameWidth()
        vertical_header = self._table.verticalHeader()
        vertical_header_width = vertical_header.width() if vertical_header.isVisible() else 0
        scrollbar_width = self._table.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        margins = self.layout().contentsMargins()
        needed_width = (
            columns_width
            + frame_width
            + vertical_header_width
            + scrollbar_width
            + margins.left()
            + margins.right()
        )

        parent = self.parent()
        if parent is not None:
            width_cap = parent.width()
        else:
            screen = QGuiApplication.primaryScreen()
            width_cap = screen.availableGeometry().width() if screen is not None else self.width()

        target_width = min(max(self.width(), needed_width), width_cap)
        if target_width > self.width():
            self.resize(target_width, self.height())

    def _selected_worktree(self) -> WorktreeInfo | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        return self._worktree_at_row(row)

    def _update_action_buttons_enabled(self) -> None:
        enabled = (not self._loading) and self._selected_worktree() is not None
        self._delete_button.setEnabled(enabled)
        self._show_changes_button.setEnabled(enabled)
        self._copy_path_button.setEnabled(enabled)
        # Row-independent: enabled whenever there is at least one real
        # worktree loaded (not just the "No linked worktrees" placeholder,
        # which never populates `_row_worktrees`) and no reload is in flight.
        self._bulk_delete_button.setEnabled((not self._loading) and len(self._row_worktrees) > 0)

    def _on_delete_button_clicked(self) -> None:
        worktree = self._selected_worktree()
        if worktree is not None:
            self._on_delete(worktree)

    def _on_show_changes_button_clicked(self) -> None:
        worktree = self._selected_worktree()
        if worktree is not None:
            self._on_show_changes(worktree)

    def _on_copy_path_button_clicked(self) -> None:
        worktree = self._selected_worktree()
        if worktree is not None:
            self._on_copy_path(worktree)

    def _on_bulk_delete_button_clicked(self) -> None:
        worktrees = list(self._row_worktrees)
        if not worktrees:
            QMessageBox.information(self, "Delete Unmodified Worktrees", "No linked worktrees.")
            return

        dialog = BulkDeleteWorktreesDialog(
            self._repo_path,
            worktrees,
            self._adapter_factory,
            parent=self,
            thread_pool=self._thread_pool,
        )
        result = dialog.exec()
        if result == QDialog.DialogCode.Accepted and dialog.deleted_paths:
            self.deleted_any = True
            self._reload()

    def _worktree_at_row(self, row: int) -> WorktreeInfo | None:
        item = self._table.item(row, 0)
        if item is None:
            return None
        return item.data(_WORKTREE_ROLE)

    def _on_delete(self, worktree: WorktreeInfo) -> None:
        confirm = QMessageBox.question(
            self,
            "Delete Worktree",
            f"Delete the worktree at:\n{worktree.path}\n\n"
            "This removes its files from disk. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        adapter = self._adapter_factory(self._repo_path)
        try:
            adapter.remove_worktree(worktree.path)
        except Exception as exc:
            force_confirm = QMessageBox.question(
                self,
                "Delete Worktree",
                f"Failed to delete cleanly (it may have uncommitted or unpushed "
                f"changes):\n{exc}\n\nForce delete anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if force_confirm != QMessageBox.StandardButton.Yes:
                return
            try:
                adapter.remove_worktree(worktree.path, force=True)
            except Exception as force_exc:
                QMessageBox.warning(
                    self, "Delete Worktree failed", f"Failed to delete worktree: {force_exc}"
                )
                return

        self.deleted_any = True
        self._reload()

    def _on_cell_double_clicked(self, row: int, _column: int) -> None:
        worktree = self._worktree_at_row(row)
        if worktree is None:
            return
        self._on_show_changes(worktree)

    def _on_context_menu_requested(self, position) -> None:
        row = self._table.rowAt(position.y())
        if row < 0:
            return
        worktree = self._worktree_at_row(row)
        if worktree is None:
            return

        menu = QMenu(self)
        menu.addAction("Delete", lambda: self._on_delete(worktree))
        menu.addAction("Show Changes", lambda: self._on_show_changes(worktree))
        menu.addAction("Copy Path", lambda: self._on_copy_path(worktree))
        # Not row-scoped (it lists every loaded worktree, not just this row)
        # but still offered here for parity with the button row -- both
        # route through the same _on_bulk_delete_button_clicked, so there is
        # one source of truth for what "Delete Unmodified…" does.
        menu.addAction("Delete Unmodified…", self._on_bulk_delete_button_clicked)
        menu.exec(self._table.viewport().mapToGlobal(position))

    def _on_show_changes(self, worktree: WorktreeInfo) -> None:
        dialog = WorktreeChangesDialog(
            worktree.path, adapter_factory=self._adapter_factory, parent=self
        )
        dialog.exec()

    def _on_copy_path(self, worktree: WorktreeInfo) -> None:
        QGuiApplication.clipboard().setText(str(worktree.path))
