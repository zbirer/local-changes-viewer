import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from local_changes_viewer.core.domain.file_history import TrackedFile, TrackedFilesResult
from local_changes_viewer.gui.workers.file_history_files_worker import FileHistoryFilesWorker


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeAdapter:
    def __init__(self, result=None, error: Exception | None = None):
        self._result = result
        self._error = error

    def list_tracked_files(self, subtree: Path) -> TrackedFilesResult:
        if self._error is not None:
            raise self._error
        return self._result


def _run_worker(worker: FileHistoryFilesWorker):
    succeeded: list = []
    errors: list[str] = []
    finished: list = []
    worker.signals.succeeded.connect(lambda result: succeeded.append(result))
    worker.signals.error.connect(lambda message: errors.append(message))
    worker.signals.finished.connect(lambda: finished.append(True))
    worker.run()
    return succeeded, errors, finished


def test_emits_result_on_success(qapp) -> None:
    expected = TrackedFilesResult(files=[TrackedFile(path=Path("a.py"), has_local_changes=False)])
    worker = FileHistoryFilesWorker(
        repo_path=Path("/repo"),
        subtree=Path("."),
        adapter_factory=lambda _path: _FakeAdapter(result=expected),
    )

    succeeded, errors, finished = _run_worker(worker)

    assert succeeded == [expected]
    assert errors == []
    assert len(finished) == 1


def test_emits_error_on_adapter_exception(qapp) -> None:
    worker = FileHistoryFilesWorker(
        repo_path=Path("/repo"),
        subtree=Path("."),
        adapter_factory=lambda _path: _FakeAdapter(error=RuntimeError("boom")),
    )

    succeeded, errors, finished = _run_worker(worker)

    assert succeeded == []
    assert errors == ["boom"]
    # `finished` is the always-fires lifetime signal WorkerKeeper releases
    # its reference on -- it must fire even on failure.
    assert len(finished) == 1
