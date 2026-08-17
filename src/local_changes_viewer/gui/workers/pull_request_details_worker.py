from PySide6.QtCore import QObject, QRunnable, Signal

from local_changes_viewer.core.infra.github_client import GitHubClient


class PullRequestDetailsWorkerSignals(QObject):
    # Renamed from `finished` -- that name is now reserved for the bare,
    # no-payload signal below that WorkerKeeper waits on to release its
    # reference; this one still only fires on success, carrying the result.
    succeeded = Signal(object)  # PullRequestDetails
    error = Signal(str)
    finished = Signal()


class PullRequestDetailsWorker(QRunnable):
    def __init__(self, github_client: GitHubClient, repository: str, number: int) -> None:
        super().__init__()
        self._github_client = github_client
        self._repository = repository
        self._number = number
        self.signals = PullRequestDetailsWorkerSignals()

    def run(self) -> None:
        try:
            try:
                # split() must stay inside the try: PullRequestInfo.repository
                # defaults to "" (core/domain/pull_request.py), and a value
                # with no "/" raises ValueError -- outside the try that would
                # escape run() straight out of the thread pool, so neither
                # `succeeded` nor `error` would ever fire and the caller
                # waits forever (though `finished` below still would).
                owner, repo = self._repository.split("/", 1)
                details = self._github_client.get_pull_request_details(owner, repo, self._number)
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                self.signals.error.emit(str(exc))
            else:
                self.signals.succeeded.emit(details)
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
