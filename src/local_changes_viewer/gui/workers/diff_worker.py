from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.domain.file_change import FileChange
from local_changes_viewer.core.services.diff_service import DiffService


class DiffWorkerSignals(QObject):
    diff_ready = Signal(object, object)  # FileChange, DiffResult
    error = Signal(str)


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
            diff = self._service.load_diff(
                self._repo_path, self._change, ignore_whitespace=self._ignore_whitespace
            )
        except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
            self.signals.error.emit(str(exc))
        else:
            self.signals.diff_ready.emit(self._change, diff)
