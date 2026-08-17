from pathlib import Path

from git.exc import NoSuchPathError
from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.domain.file_change import FileChange
from local_changes_viewer.core.services.diff_service import DiffService


class DiffWorkerSignals(QObject):
    diff_ready = Signal(object, object)  # FileChange, DiffResult
    error = Signal(str)
    # Always emitted last (see run()) -- this is what WorkerKeeper waits for
    # before releasing its reference, never a payload-carrying signal.
    finished = Signal()


class DiffWorker(QRunnable):
    def __init__(
        self, repo_path: Path, change: FileChange, ignore_whitespace: bool = False
    ) -> None:
        super().__init__()
        self._repo_path = repo_path
        self._change = change
        self._ignore_whitespace = ignore_whitespace
        self._service = DiffService()
        self.signals = DiffWorkerSignals()

    def run(self) -> None:
        try:
            try:
                diff = self._service.load_diff(
                    self._repo_path, self._change, ignore_whitespace=self._ignore_whitespace
                )
            except NoSuchPathError:
                # git.Repo(repo_path), which DiffService's GitRepoAdapter
                # constructs on every call, raises this (a subclass of OSError
                # whose __str__ returns only the bare repo path) when the repo's
                # directory has been deleted from disk -- e.g. a worktree removed
                # outside the app. Bare str(exc) used to surface as just that
                # path with no explanation and no mention of what the user
                # clicked; spell it out plainly and name the file.
                message = (
                    f"Repository folder no longer exists: {self._repo_path} "
                    f"(file: {self._change.path})"
                )
                self.signals.error.emit(message)
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                self.signals.error.emit(
                    f"{type(exc).__name__}: {exc} (file: {self._change.path})"
                )
            else:
                self.signals.diff_ready.emit(self._change, diff)
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
