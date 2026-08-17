from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.gui.workspace_watcher import collect_watch_paths


class WatchPathsWorkerSignals(QObject):
    # Renamed from `finished` -- that name is now reserved for the bare,
    # no-payload signal below that WorkerKeeper waits on to release its
    # reference; this one still only fires on success, carrying the result.
    succeeded = Signal(object)  # list[Path]
    error = Signal(str)
    finished = Signal()


class WatchPathsWorker(QRunnable):
    """Walks repo directories off the UI thread to find paths to watch."""

    def __init__(self, repo_paths: list[Path]) -> None:
        super().__init__()
        self._repo_paths = repo_paths
        self.signals = WatchPathsWorkerSignals()

    def run(self) -> None:
        try:
            try:
                watch_paths = collect_watch_paths(self._repo_paths)
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                self.signals.error.emit(str(exc))
            else:
                self.signals.succeeded.emit(watch_paths)
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
