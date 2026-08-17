import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from local_changes_viewer.gui.workers.pull_request_threads_worker import (
    PullRequestThreadsWorker,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_repository_with_no_slash_emits_error_instead_of_raising(qapp) -> None:
    """Regression test: `owner, repo = self._repository.split("/", 1)` used to
    sit outside the try/except, so a repository string with no "/" (the
    default for PullRequestInfo.repository, see
    core/domain/pull_request.py) raised ValueError straight out of run() --
    neither `finished` nor `error` ever fired, and the requesting UI waited
    forever for a signal that would never come.
    """
    worker = PullRequestThreadsWorker(github_client=object(), repository="no-slash-here", number=1)
    errors: list[str] = []
    succeeded: list[tuple] = []
    finished: list[tuple] = []
    worker.signals.error.connect(lambda message: errors.append(message))
    worker.signals.succeeded.connect(lambda *args: succeeded.append(args))
    worker.signals.finished.connect(lambda *args: finished.append(args))

    worker.run()  # must not raise

    assert len(errors) == 1
    assert succeeded == []
    # `finished` (unlike `succeeded`) is the always-fires lifetime signal
    # WorkerKeeper releases its reference on -- it must fire even here.
    assert len(finished) == 1
