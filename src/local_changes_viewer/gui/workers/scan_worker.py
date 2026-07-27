from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.core.infra.github_client import GitHubClient
from local_changes_viewer.core.services.workspace_scanner_service import (
    WorkspaceScannerService,
)


class ScanWorkerSignals(QObject):
    workspace_ready = Signal(object)  # Workspace
    repo_ready = Signal(object)  # Repository
    error = Signal(str)
    progress = Signal(str)
    log_message = Signal(str)


class ScanWorker(QRunnable):
    def __init__(
        self,
        root: Path,
        include_ignored: bool = False,
        github_client: GitHubClient | None = None,
        previous_pull_requests: dict[Path, tuple[PullRequestInfo, str]] | None = None,
    ) -> None:
        super().__init__()
        self._root = root
        self._include_ignored = include_ignored
        self._github_client = github_client
        self._previous_pull_requests = previous_pull_requests
        self._service = WorkspaceScannerService()
        self.signals = ScanWorkerSignals()

    def run(self) -> None:
        try:
            workspace = self._service.scan(
                self._root,
                include_ignored=self._include_ignored,
                on_progress=self.signals.progress.emit,
                on_repo_ready=self.signals.repo_ready.emit,
                github_client=self._github_client,
                on_log=self.signals.log_message.emit,
                previous_pull_requests=self._previous_pull_requests,
            )
        except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
            self.signals.error.emit(str(exc))
        else:
            self.signals.workspace_ready.emit(workspace)
