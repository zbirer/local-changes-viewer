"""Deletes a single worktree off the GUI thread for WorktreesDialog's
per-row "Delete" action (see `_on_delete`). `remove_worktree()` shells out to
`git worktree remove`, which can hang for a while on a slow disk or network
share -- calling it straight from `_on_delete` used to freeze the whole app
with no busy indicator and no way to cancel.

Deliberately its own worker rather than reusing BulkWorktreeDeleteWorker: the
per-row Delete action's existing force-delete retry (on a dirty/uncommitted
worktree) needs `remove_worktree(path, force=True)`, and the bulk worker is
hardcoded to force=False on purpose (see its own docstring) -- there is no
`force` knob to reuse there without changing behavior no caller asked for.
"""

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal


class WorktreeDeleteWorkerSignals(QObject):
    # Renamed from `finished` -- that name is now reserved for the bare
    # signal below that WorkerKeeper waits on to release its reference;
    # this one still only fires on success (both take no arguments, but
    # they are not interchangeable: this one means "delete succeeded").
    succeeded = Signal()
    error = Signal(str)
    finished = Signal()


class WorktreeDeleteWorker(QRunnable):
    def __init__(
        self,
        repo_path: Path,
        adapter_factory: Callable[[Path], object],
        worktree_path: Path,
        force: bool = False,
    ) -> None:
        super().__init__()
        self._repo_path = repo_path
        self._adapter_factory = adapter_factory
        self._worktree_path = worktree_path
        self._force = force
        self.signals = WorktreeDeleteWorkerSignals()

    def run(self) -> None:
        try:
            try:
                adapter = self._adapter_factory(self._repo_path)
                adapter.remove_worktree(self._worktree_path, force=self._force)
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                self.signals.error.emit(str(exc))
            else:
                self.signals.succeeded.emit()
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
