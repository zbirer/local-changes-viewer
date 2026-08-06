from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.gui.workspace_watcher import collect_watch_paths


class WatchPathsWorkerSignals(QObject):
    finished = Signal(object)  # list[Path]


class WatchPathsWorker(QRunnable):
    """Walks repo directories off the UI thread to find paths to watch."""

    def __init__(self, repo_paths: list[Path]) -> None:
        super().__init__()
        self._repo_paths = repo_paths
        self.signals = WatchPathsWorkerSignals()

    def run(self) -> None:
        watch_paths = collect_watch_paths(self._repo_paths)
        self.signals.finished.emit(watch_paths)
