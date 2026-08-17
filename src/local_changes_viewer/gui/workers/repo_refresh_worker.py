from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.core.infra.github_client import GitHubClient
from local_changes_viewer.core.services.workspace_scanner_service import (
    WorkspaceScannerService,
)


class RepoRefreshWorkerSignals(QObject):
    repo_ready = Signal(object)  # Repository | None
    error = Signal(str)
    log_message = Signal(str)
    # Always emitted last (see run()) -- this is what WorkerKeeper waits for
    # before releasing its reference, never a payload-carrying signal.
    finished = Signal()


class RepoRefreshWorker(QRunnable):
    def __init__(
        self,
        repo_path: Path,
        include_ignored: bool = False,
        github_client: GitHubClient | None = None,
        previous_pull_request: tuple[PullRequestInfo, str] | None = None,
        logical_parent_path: Path | None = None,
        include_unpushed_commits: bool = False,
    ) -> None:
        super().__init__()
        self._repo_path = repo_path
        self._include_ignored = include_ignored
        self._github_client = github_client
        self._previous_pull_request = previous_pull_request
        self._logical_parent_path = logical_parent_path
        self._include_unpushed_commits = include_unpushed_commits
        self._service = WorkspaceScannerService()
        self.signals = RepoRefreshWorkerSignals()

    def run(self) -> None:
        try:
            try:
                repo = self._service.scan_repo(
                    self._repo_path,
                    include_ignored=self._include_ignored,
                    github_client=self._github_client,
                    on_log=self.signals.log_message.emit,
                    previous_pull_request=self._previous_pull_request,
                    logical_parent_path=self._logical_parent_path,
                    include_unpushed_commits=self._include_unpushed_commits,
                )
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                self.signals.error.emit(str(exc))
            else:
                self.signals.repo_ready.emit(repo)
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
