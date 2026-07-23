from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.services.workspace_scanner_service import (
    WorkspaceScannerService,
)


class ScanWorkerSignals(QObject):
    workspace_ready = Signal(object)  # Workspace
    repo_ready = Signal(object)  # Repository
    error = Signal(str)
    progress = Signal(str)


class ScanWorker(QRunnable):
    def __init__(self, root: Path, include_ignored: bool = False) -> None:
        super().__init__()
        self._root = root
        self._include_ignored = include_ignored
        self._service = WorkspaceScannerService()
        self.signals = ScanWorkerSignals()

    def run(self) -> None:
        try:
            workspace = self._service.scan(
                self._root,
                include_ignored=self._include_ignored,
                on_progress=self.signals.progress.emit,
                on_repo_ready=self.signals.repo_ready.emit,
            )
        except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
            self.signals.error.emit(str(exc))
        else:
            self.signals.workspace_ready.emit(workspace)
