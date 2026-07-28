from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.infra.github_client import GitHubClient


class MyPullRequestsWorkerSignals(QObject):
    finished = Signal(list)  # list[PullRequestInfo]
    error = Signal(str)
    progress = Signal(str)


class MyPullRequestsWorker(QRunnable):
    def __init__(
        self,
        github_client: GitHubClient,
        username: str,
        owner_repo_pairs: list[tuple[str, str]],
    ) -> None:
        super().__init__()
        self._github_client = github_client
        self._username = username
        self._owner_repo_pairs = owner_repo_pairs
        self.signals = MyPullRequestsWorkerSignals()

    def run(self) -> None:
        try:
            pull_requests = self._github_client.list_authored_open_pull_requests(
                self._username, self._owner_repo_pairs, on_progress=self.signals.progress.emit
            )
        except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
            self.signals.error.emit(str(exc))
        else:
            self.signals.finished.emit(pull_requests)
