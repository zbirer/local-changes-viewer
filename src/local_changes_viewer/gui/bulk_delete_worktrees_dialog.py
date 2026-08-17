"""Selection + progress dialog for WorktreesDialog's "Delete Unmodified…" bulk
action (see `_on_bulk_delete_button_clicked`). Lists every linked worktree --
not just the unmodified ones -- pre-checking the ones with no unpushed
changes so the common case ("clear out everything I'm done with") is a single
Delete click, while still letting the user deliberately sweep in a worktree
that does have unpushed changes.

Deletion always goes through `remove_worktree(path, force=False)` -- never
force=True, even in bulk. A failed removal (dirty worktree, in-use, etc.) is
reported in the post-run summary telling the user to delete that one
individually via the per-row Delete action, which already has its own
force-delete prompt (see `WorktreesDialog._on_delete`). A bulk force-delete
fallback would turn one "oops, wrong checkbox" into silent data loss across
several worktrees at once, so it is deliberately not offered here.
"""

from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.worktree_info import WorktreeInfo
from local_changes_viewer.gui.workers.bulk_worktree_delete_worker import BulkWorktreeDeleteWorker
from local_changes_viewer.gui.workers.worker_keeper import start_worker

_WORKTREE_ROLE = Qt.ItemDataRole.UserRole
# Amber, not red: an unpushed worktree is still a legitimate, deliberate
# choice to delete (the user may just want to reclaim disk space), not an
# error state -- this is a heads-up, not a block.
_UNPUSHED_WARNING_FOREGROUND = QColor("#f59e0b")


class BulkDeleteWorktreesDialog(QDialog):
    def __init__(
        self,
        repo_path: Path,
        worktrees: list[WorktreeInfo],
        adapter_factory,
        parent: QWidget | None = None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self._repo_path = repo_path
        self._adapter_factory = adapter_factory
        self._thread_pool = thread_pool if thread_pool is not None else QThreadPool.globalInstance()
        self.setWindowTitle("Delete Unmodified Worktrees")
        parent_width = parent.width() if parent is not None else 700
        self.resize(int(parent_width * 0.6), 420)

        # Outcome, readable by both the caller (to decide whether to reload)
        # and tests, once the worker's `finished` signal has fired.
        self.deleted_paths: list[Path] = []
        self.failed: list[tuple[Path, str]] = []

        self._list = QListWidget()
        for worktree in worktrees:
            item = QListWidgetItem(self._row_text(worktree))
            item.setData(_WORKTREE_ROLE, worktree)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if worktree.has_unpushed_changes:
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setForeground(_UNPUSHED_WARNING_FOREGROUND)
            else:
                item.setCheckState(Qt.CheckState.Checked)
            self._list.addItem(item)
        # Connected only after every row is populated -- each addItem() above
        # would otherwise itself fire a change event, running the count-label
        # update once per row before the initial state is even fully built.
        self._list.itemChanged.connect(self._on_item_changed)

        self._select_all_button = QPushButton("Select All")
        self._select_none_button = QPushButton("Select None")
        self._select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self._select_none_button.clicked.connect(lambda: self._set_all_checked(False))
        select_row = QHBoxLayout()
        select_row.addWidget(self._select_all_button)
        select_row.addWidget(self._select_none_button)
        select_row.addStretch(1)

        self._count_label = QLabel()
        self._status_label = QLabel()
        self._status_label.hide()

        self._delete_button = QPushButton("Delete")
        self._cancel_button = QPushButton("Cancel")
        self._delete_button.clicked.connect(self._on_delete_clicked)
        self._cancel_button.clicked.connect(self.reject)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self._delete_button)
        button_row.addWidget(self._cancel_button)

        layout = QVBoxLayout(self)
        layout.addLayout(select_row)
        layout.addWidget(self._list)
        layout.addWidget(self._count_label)
        layout.addWidget(self._status_label)
        layout.addLayout(button_row)

        self._update_count_label()
        self._update_delete_enabled()

    @staticmethod
    def _row_text(worktree: WorktreeInfo) -> str:
        text = str(worktree.path)
        if worktree.has_unpushed_changes:
            text += "  — has unpushed changes"
        return text

    def _checked_items(self) -> list[QListWidgetItem]:
        return [
            self._list.item(i)
            for i in range(self._list.count())
            if self._list.item(i).checkState() == Qt.CheckState.Checked
        ]

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        # Blocked while flipping every row so itemChanged doesn't run the
        # count/enabled recompute once per row -- the explicit calls below
        # cover the settled state once, after every row has been updated.
        self._list.blockSignals(True)
        for i in range(self._list.count()):
            self._list.item(i).setCheckState(state)
        self._list.blockSignals(False)
        self._update_count_label()
        self._update_delete_enabled()

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        self._update_count_label()
        self._update_delete_enabled()

    def _update_count_label(self) -> None:
        checked = len(self._checked_items())
        total = self._list.count()
        self._count_label.setText(f"{checked} of {total} selected")

    def _update_delete_enabled(self) -> None:
        self._delete_button.setEnabled(len(self._checked_items()) > 0)

    def _on_delete_clicked(self) -> None:
        checked = self._checked_items()
        worktrees: list[WorktreeInfo] = [item.data(_WORKTREE_ROLE) for item in checked]
        unpushed = [w for w in worktrees if w.has_unpushed_changes]
        if unpushed:
            names = "\n".join(f"  {w.path}" for w in unpushed)
            confirm = QMessageBox.warning(
                self,
                "Delete Unmodified Worktrees",
                "The following selected worktrees have unpushed changes:\n\n"
                f"{names}\n\n"
                "Delete them anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        self._start_delete([w.path for w in worktrees])

    def _start_delete(self, paths: list[Path]) -> None:
        # Same reasoning as WorktreesDialog._reload's table-disable: the list
        # and every button here must not be touchable again until this batch
        # (running on the thread pool) reports back, or a second Delete could
        # fire against paths already mid-removal.
        self._list.setEnabled(False)
        self._select_all_button.setEnabled(False)
        self._select_none_button.setEnabled(False)
        self._delete_button.setEnabled(False)
        self._cancel_button.setEnabled(False)
        self._status_label.show()

        worker = BulkWorktreeDeleteWorker(self._repo_path, self._adapter_factory, paths)
        worker.signals.progress.connect(self._on_progress)
        worker.signals.one_finished.connect(self._on_one_finished)
        worker.signals.finished.connect(self._on_all_finished)
        start_worker(self._thread_pool, worker)

    def _on_progress(self, index: int, total: int, path_str: str) -> None:
        name = Path(path_str).name
        self._status_label.setText(f"Deleting {index} of {total}: {name} …")

    def _on_one_finished(self, path_str: str, error: str) -> None:
        path = Path(path_str)
        if error:
            self.failed.append((path, error))
        else:
            self.deleted_paths.append(path)

    def _on_all_finished(self) -> None:
        if self.failed:
            names = "\n".join(f"  {path}: {error}" for path, error in self.failed)
            QMessageBox.warning(
                self,
                "Some Worktrees Not Deleted",
                "The following worktrees could not be deleted. Delete them "
                "individually to see the force-delete option:\n\n"
                f"{names}",
            )
        self.accept()
