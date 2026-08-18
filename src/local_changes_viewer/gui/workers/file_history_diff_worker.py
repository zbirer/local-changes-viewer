"""Loads a File History diff (either mode) off the GUI thread.

Cancellable: selecting a different commit or flipping the mode radio cancels
the outgoing request's `CancelToken` before starting the replacement -- see
`CancelToken` for why `cancel()` only kills the subprocess and flips a flag,
never frees anything QRunnable-owned.

Mode A ("Changes in this commit") calls `get_commit_file_diff`, which is
unchanged by this feature and takes no `cancel_token` -- `git show` on one
file's one commit is not the unbounded operation cancellation exists for.
This worker still honours a token passed in for that mode: it will not start
the call at all if already cancelled, and it re-checks before emitting, the
same as mode B.
"""

from enum import Enum, auto
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.core.infra.cancel_token import CancelToken, GitCancelled


class FileHistoryDiffMode(Enum):
    COMMIT = auto()  # "Changes in this commit" -- get_commit_file_diff, unchanged
    AGAINST_DISK = auto()  # "Compared to file on disk" -- get_file_diff_against_disk


class FileHistoryDiffWorkerSignals(QObject):
    succeeded = Signal(object)  # DiffResult
    error = Signal(str)
    finished = Signal()


class FileHistoryDiffWorker(QRunnable):
    def __init__(
        self,
        repo_path: Path,
        mode: FileHistoryDiffMode,
        commit_hexsha: str,
        path_at_commit: Path,
        adapter_factory: Callable[[Path], object],
        renamed_from: Path | None = None,
        current_path: Path | None = None,
        cancel_token: CancelToken | None = None,
    ) -> None:
        super().__init__()
        self._repo_path = repo_path
        self._mode = mode
        self._commit_hexsha = commit_hexsha
        self._path_at_commit = path_at_commit
        self._adapter_factory = adapter_factory
        self._renamed_from = renamed_from
        self._current_path = current_path
        self._cancel_token = cancel_token
        self.signals = FileHistoryDiffWorkerSignals()

    def run(self) -> None:
        try:
            try:
                if self._cancel_token is not None and self._cancel_token.is_cancelled:
                    raise GitCancelled()
                adapter = self._adapter_factory(self._repo_path)
                if self._mode is FileHistoryDiffMode.COMMIT:
                    diff: DiffResult = adapter.get_commit_file_diff(
                        self._commit_hexsha, self._path_at_commit, self._renamed_from
                    )
                else:
                    diff = adapter.get_file_diff_against_disk(
                        self._commit_hexsha,
                        self._path_at_commit,
                        self._current_path,
                        cancel_token=self._cancel_token,
                    )
            except GitCancelled:
                # Swallowed deliberately -- see file_history_commits_worker.py
                # for the full reasoning (GitCancelled must be caught before
                # the blanket handler right below it).
                pass
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                self.signals.error.emit(str(exc))
            else:
                # A second, independent guard against a stale result: a
                # cancel() can land after the subprocess already returned but
                # before this line runs.
                if self._cancel_token is None or not self._cancel_token.is_cancelled:
                    self.signals.succeeded.emit(diff)
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
