"""Loads a repo's `list_worktree_details()` off the GUI thread.

`list_worktree_details()` shells out to git once per linked worktree (branch,
last-activity, ahead/behind upstream), which is fast for one worktree but was
freezing the whole app for a few seconds with several -- WorktreesDialog used
to call it straight from `__init__` (and again after every delete) on the GUI
thread. Moving it here, mirroring RepoRefreshWorker/DiffWorker's
QRunnable-plus-signals shape, lets the dialog show a "Reading data ..."
placeholder instead of freezing.
"""

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.domain.worktree_info import WorktreeInfo


class WorktreeDetailsWorkerSignals(QObject):
    # Renamed from `finished` -- that name is now reserved for the bare,
    # no-payload signal below that WorkerKeeper waits on to release its
    # reference; this one still only fires on success, carrying the result.
    succeeded = Signal(list)  # list[WorktreeInfo]
    error = Signal(str)
    finished = Signal()


class WorktreeDetailsWorker(QRunnable):
    def __init__(self, repo_path: Path, adapter_factory: Callable[[Path], object]) -> None:
        super().__init__()
        self._repo_path = repo_path
        self._adapter_factory = adapter_factory
        self.signals = WorktreeDetailsWorkerSignals()

    def run(self) -> None:
        try:
            try:
                details: list[WorktreeInfo] = list(
                    self._adapter_factory(self._repo_path).list_worktree_details()
                )
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                self.signals.error.emit(str(exc))
            else:
                self.signals.succeeded.emit(details)
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
