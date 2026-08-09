from collections.abc import Callable
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
    debug_message = Signal(str)


class ScanWorker(QRunnable):
    def __init__(
        self,
        root: Path,
        include_ignored: bool = False,
        github_client: GitHubClient | None = None,
        previous_pull_requests: dict[Path, tuple[PullRequestInfo, str]] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        profile_repo_names: set[str] | None = None,
        include_unpushed_commits: bool = False,
        dirty_paths: set[Path] | None = None,
        force_full_rescan: bool = False,
        service: WorkspaceScannerService | None = None,
    ) -> None:
        super().__init__()
        self._root = root
        self._include_ignored = include_ignored
        self._github_client = github_client
        self._previous_pull_requests = previous_pull_requests
        self._is_cancelled = is_cancelled
        self._profile_repo_names = profile_repo_names
        self._include_unpushed_commits = include_unpushed_commits
        self._dirty_paths = dirty_paths
        self._force_full_rescan = force_full_rescan
        # A shared service instance (kept alive across scans by the caller) is
        # what makes the cross-scan repo/PR caching in WorkspaceScannerService
        # possible; falling back to a fresh instance just disables caching.
        self._service = service or WorkspaceScannerService()
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
                is_cancelled=self._is_cancelled,
                profile_repo_names=self._profile_repo_names,
                include_unpushed_commits=self._include_unpushed_commits,
                dirty_paths=self._dirty_paths,
                force_full_rescan=self._force_full_rescan,
                on_debug=self.signals.debug_message.emit,
            )
        except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
            self.signals.error.emit(str(exc))
        else:
            self.signals.workspace_ready.emit(workspace)
