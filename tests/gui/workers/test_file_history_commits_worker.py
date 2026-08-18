import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from local_changes_viewer.core.domain.file_history import FileHistoryResult
from local_changes_viewer.core.infra.cancel_token import CancelToken
from local_changes_viewer.gui.workers.file_history_commits_worker import FileHistoryCommitsWorker


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeAdapter:
    """A fake whose `get_file_history` behaves according to `behavior`:

    - "succeed": returns `result` immediately.
    - "raise": raises `error` immediately.
    - "via_token": calls straight through to the real `cancel_token.run(...)`,
      so tests can exercise the actual CancelToken contract (raising
      GitCancelled when already cancelled, or when cancelled mid-flight)
      through the worker rather than faking that behaviour separately.
    """

    def __init__(self, behavior: str, result=None, error: Exception | None = None, args=None):
        self._behavior = behavior
        self._result = result
        self._error = error
        self._args = args

    def get_file_history(
        self, path: Path, limit: int = 10, cancel_token: CancelToken | None = None
    ) -> FileHistoryResult:
        if self._behavior == "succeed":
            return self._result
        if self._behavior == "raise":
            raise self._error
        return cancel_token.run(self._args, cwd=Path("."))


def _run_worker(worker: FileHistoryCommitsWorker):
    succeeded: list = []
    errors: list[str] = []
    finished: list = []
    worker.signals.succeeded.connect(lambda result: succeeded.append(result))
    worker.signals.error.connect(lambda message: errors.append(message))
    worker.signals.finished.connect(lambda: finished.append(True))
    worker.run()
    return succeeded, errors, finished


def test_emits_result_on_success(qapp) -> None:
    expected = FileHistoryResult()
    worker = FileHistoryCommitsWorker(
        repo_path=Path("/repo"),
        file_path=Path("a.py"),
        cancel_token=CancelToken(),
        adapter_factory=lambda _path: _FakeAdapter(behavior="succeed", result=expected),
    )

    succeeded, errors, finished = _run_worker(worker)

    assert succeeded == [expected]
    assert errors == []
    assert len(finished) == 1


def test_emits_error_on_adapter_exception(qapp) -> None:
    worker = FileHistoryCommitsWorker(
        repo_path=Path("/repo"),
        file_path=Path("a.py"),
        cancel_token=CancelToken(),
        adapter_factory=lambda _path: _FakeAdapter(behavior="raise", error=RuntimeError("boom")),
    )

    succeeded, errors, finished = _run_worker(worker)

    assert succeeded == []
    assert errors == ["boom"]
    assert len(finished) == 1


def test_cancel_before_run_suppresses_everything_but_finished(qapp, tmp_path: Path) -> None:
    token = CancelToken()
    token.cancel()
    # A marker file the process would create if it were ever spawned --
    # proves nothing ran at all, not just that no signal fired.
    marker = tmp_path / "marker"
    worker = FileHistoryCommitsWorker(
        repo_path=Path("/repo"),
        file_path=Path("a.py"),
        cancel_token=token,
        adapter_factory=lambda _path: _FakeAdapter(
            behavior="via_token",
            args=[sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"],
        ),
    )

    succeeded, errors, finished = _run_worker(worker)

    assert succeeded == []
    assert errors == []
    assert len(finished) == 1
    assert not marker.exists()


def test_cancel_mid_flight_raises_git_cancelled_and_emits_neither_succeeded_nor_error(
    qapp, tmp_path: Path
) -> None:
    marker = tmp_path / "started.marker"
    script = tmp_path / "block.py"
    script.write_text(
        f"import pathlib, time\n"
        f"pathlib.Path({str(marker)!r}).write_text('x')\n"
        f"time.sleep(30)\n"
    )

    token = CancelToken()
    worker = FileHistoryCommitsWorker(
        repo_path=Path("/repo"),
        file_path=Path("a.py"),
        cancel_token=token,
        adapter_factory=lambda _path: _FakeAdapter(
            behavior="via_token", args=[sys.executable, str(script)]
        ),
    )

    succeeded: list = []
    errors: list[str] = []
    finished: list = []
    # DirectConnection: run() executes on a background thread here, and a
    # connection to a plain Python callable made from the main thread would
    # otherwise be queued to that thread's event loop -- which nothing here
    # pumps. This is a test-harness concern, not something the worker itself
    # needs to care about (the real GUI does run an event loop).
    worker.signals.succeeded.connect(
        lambda result: succeeded.append(result), Qt.ConnectionType.DirectConnection
    )
    worker.signals.error.connect(
        lambda message: errors.append(message), Qt.ConnectionType.DirectConnection
    )
    worker.signals.finished.connect(
        lambda: finished.append(True), Qt.ConnectionType.DirectConnection
    )

    worker_thread = threading.Thread(target=worker.run)
    started = time.monotonic()
    worker_thread.start()

    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.exists(), "subprocess never signalled that it started"

    token.cancel()
    worker_thread.join(timeout=5)
    elapsed = time.monotonic() - started

    assert not worker_thread.is_alive()
    assert elapsed < 10, "cancel() did not actually kill the subprocess"
    assert succeeded == []
    assert errors == []
    assert len(finished) == 1
