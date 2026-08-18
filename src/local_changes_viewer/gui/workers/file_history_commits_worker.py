"""Loads a file's commit history for File History off the GUI thread.

Cancellable: selecting a different file cancels the outgoing request's
`CancelToken` before starting the replacement (see `CancelToken` for why
`cancel()` only kills the subprocess and flips a flag, never frees anything
QRunnable-owned).
"""

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.domain.file_history import FileHistoryResult
from local_changes_viewer.core.infra.cancel_token import CancelToken, GitCancelled


class FileHistoryCommitsWorkerSignals(QObject):
    succeeded = Signal(object)  # FileHistoryResult
    error = Signal(str)
    finished = Signal()


class FileHistoryCommitsWorker(QRunnable):
    def __init__(
        self,
        repo_path: Path,
        file_path: Path,
        cancel_token: CancelToken,
        adapter_factory: Callable[[Path], object],
        limit: int = 10,
    ) -> None:
        super().__init__()
        self._repo_path = repo_path
        self._file_path = file_path
        self._cancel_token = cancel_token
        self._adapter_factory = adapter_factory
        self._limit = limit
        self.signals = FileHistoryCommitsWorkerSignals()

    def run(self) -> None:
        try:
            try:
                result: FileHistoryResult = self._adapter_factory(
                    self._repo_path
                ).get_file_history(
                    self._file_path, limit=self._limit, cancel_token=self._cancel_token
                )
            except GitCancelled:
                # Swallowed deliberately: cancel() only kills the subprocess
                # and flips a flag, run() still finishes normally and always
                # emits `finished` below -- but neither `succeeded` nor
                # `error` fires, so a routine "user picked something else"
                # never surfaces as a failure. GitCancelled IS an Exception
                # subclass, so this must be caught *before* the blanket
                # handler right below it, or it would fall through to
                # `error` and silently break that contract.
                pass
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                self.signals.error.emit(str(exc))
            else:
                # A second, independent guard against a stale result:
                # CancelToken.run() only catches a cancellation up to its
                # own last internal check, so cancel() can still land after
                # the subprocess already returned but before this line runs.
                if not self._cancel_token.is_cancelled:
                    self.signals.succeeded.emit(result)
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
