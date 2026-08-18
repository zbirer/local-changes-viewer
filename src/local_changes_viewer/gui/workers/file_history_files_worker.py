"""Loads a folder's tracked-file list for File History off the GUI thread.

Not cancellable -- one shot per dialog open (`list_tracked_files` already
caps itself at `_FILE_HISTORY_SUBTREE_FILE_CAP` tracked files, so there's no
unbounded operation here to interrupt).
"""

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.domain.file_history import TrackedFilesResult


class FileHistoryFilesWorkerSignals(QObject):
    succeeded = Signal(object)  # TrackedFilesResult
    error = Signal(str)
    finished = Signal()


class FileHistoryFilesWorker(QRunnable):
    def __init__(
        self, repo_path: Path, subtree: Path, adapter_factory: Callable[[Path], object]
    ) -> None:
        super().__init__()
        self._repo_path = repo_path
        self._subtree = subtree
        self._adapter_factory = adapter_factory
        self.signals = FileHistoryFilesWorkerSignals()

    def run(self) -> None:
        try:
            try:
                result: TrackedFilesResult = self._adapter_factory(
                    self._repo_path
                ).list_tracked_files(self._subtree)
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                self.signals.error.emit(str(exc))
            else:
                self.signals.succeeded.emit(result)
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
