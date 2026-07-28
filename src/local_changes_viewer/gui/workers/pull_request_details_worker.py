from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.infra.github_client import GitHubClient


class PullRequestDetailsWorkerSignals(QObject):
    finished = Signal(object)  # PullRequestDetails
    error = Signal(str)


class PullRequestDetailsWorker(QRunnable):
    def __init__(self, github_client: GitHubClient, repository: str, number: int) -> None:
        super().__init__()
        self._github_client = github_client
        self._repository = repository
        self._number = number
        self.signals = PullRequestDetailsWorkerSignals()

    def run(self) -> None:
        owner, repo = self._repository.split("/", 1)
        try:
            details = self._github_client.get_pull_request_details(owner, repo, self._number)
        except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
            self.signals.error.emit(str(exc))
        else:
            self.signals.finished.emit(details)
