"""Deletes a batch of worktrees off the GUI thread for the "Delete Unmodified…"
bulk-delete flow (see BulkDeleteWorktreesDialog). Mirrors WorktreeDetailsWorker's
QRunnable-plus-signals shape, but one failed removal must not abort the rest of
the batch -- so each worktree's outcome is reported individually via
`one_finished` rather than the whole run failing (and stopping) on the first
exception.
"""

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal


class BulkWorktreeDeleteWorkerSignals(QObject):
    progress = Signal(int, int, str)  # 1-based index, total, worktree path str
    one_finished = Signal(str, str)  # worktree path str, error string ("" on success)
    # Batch-complete regardless of per-item outcome (see run()) -- this is
    # also what WorkerKeeper waits on to release its reference to this
    # worker, so it stays bare/no-payload rather than carrying a result.
    finished = Signal()


class BulkWorktreeDeleteWorker(QRunnable):
    def __init__(
        self,
        repo_path: Path,
        adapter_factory: Callable[[Path], object],
        paths: list[Path],
    ) -> None:
        super().__init__()
        self._repo_path = repo_path
        self._adapter_factory = adapter_factory
        self._paths = list(paths)
        self.signals = BulkWorktreeDeleteWorkerSignals()

    def run(self) -> None:
        total = len(self._paths)
        try:
            try:
                adapter = self._adapter_factory(self._repo_path)
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                # Constructing the adapter is itself outside the per-item
                # loop's try/except below -- without this, a bad repo path
                # or missing git binary would raise straight out of run(),
                # `one_finished` would never fire for any path, and
                # BulkDeleteWorktreesDialog would be stuck forever (list/
                # buttons disabled, no way to close it). Reporting every
                # requested path as failed keeps the dialog's contract
                # ("`finished` always fires, failures land in `failed`")
                # intact; the outer finally below still covers `finished`.
                for index, path in enumerate(self._paths, start=1):
                    self.signals.progress.emit(index, total, str(path))
                    self.signals.one_finished.emit(str(path), str(exc))
                return

            for index, path in enumerate(self._paths, start=1):
                self.signals.progress.emit(index, total, str(path))
                # Deliberately force=False (see remove_worktree) -- a
                # failure here is reported per-item below rather than
                # raised, so one dirty worktree never aborts the rest of
                # the batch.
                try:
                    adapter.remove_worktree(path)
                except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                    self.signals.one_finished.emit(str(path), str(exc))
                else:
                    self.signals.one_finished.emit(str(path), "")
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
