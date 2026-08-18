import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.core.infra.cancel_token import CancelToken
from local_changes_viewer.gui.workers.file_history_diff_worker import (
    FileHistoryDiffMode,
    FileHistoryDiffWorker,
)


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeAdapter:
    """Fake standing in for both diff modes.

    `commit_behavior`/`disk_behavior` are each one of "succeed", "raise", or
    "via_token" (the last calls straight through to a real cancel_token.run,
    so a test can exercise the actual CancelToken contract end to end).
    """

    def __init__(
        self,
        behavior: str = "succeed",
        result=None,
        error: Exception | None = None,
        args=None,
    ):
        self._behavior = behavior
        self._result = result
        self._error = error
        self._args = args

    def _resolve(self, cancel_token: CancelToken | None = None):
        if self._behavior == "succeed":
            return self._result
        if self._behavior == "raise":
            raise self._error
        return cancel_token.run(self._args, cwd=Path("."))

    def get_commit_file_diff(self, commit_hexsha, file_path, old_path=None):
        return self._resolve()

    def get_file_diff_against_disk(
        self, commit_hexsha, path_at_commit, current_path, cancel_token=None
    ):
        return self._resolve(cancel_token)


def _run_worker(worker: FileHistoryDiffWorker):
    succeeded: list = []
    errors: list[str] = []
    finished: list = []
    worker.signals.succeeded.connect(lambda result: succeeded.append(result))
    worker.signals.error.connect(lambda message: errors.append(message))
    worker.signals.finished.connect(lambda: finished.append(True))
    worker.run()
    return succeeded, errors, finished


def test_mode_commit_emits_result_on_success(qapp) -> None:
    expected = DiffResult(old_ref="abc", new_ref="def")
    worker = FileHistoryDiffWorker(
        repo_path=Path("/repo"),
        mode=FileHistoryDiffMode.COMMIT,
        commit_hexsha="abc123",
        path_at_commit=Path("a.py"),
        adapter_factory=lambda _path: _FakeAdapter(behavior="succeed", result=expected),
    )

    succeeded, errors, finished = _run_worker(worker)

    assert succeeded == [expected]
    assert errors == []
    assert len(finished) == 1


def test_mode_commit_emits_error_on_adapter_exception(qapp) -> None:
    worker = FileHistoryDiffWorker(
        repo_path=Path("/repo"),
        mode=FileHistoryDiffMode.COMMIT,
        commit_hexsha="abc123",
        path_at_commit=Path("a.py"),
        adapter_factory=lambda _path: _FakeAdapter(behavior="raise", error=RuntimeError("boom")),
    )

    succeeded, errors, finished = _run_worker(worker)

    assert succeeded == []
    assert errors == ["boom"]
    assert len(finished) == 1


def test_mode_against_disk_emits_result_on_success(qapp) -> None:
    expected = DiffResult(old_ref="abc", new_ref="working tree")
    worker = FileHistoryDiffWorker(
        repo_path=Path("/repo"),
        mode=FileHistoryDiffMode.AGAINST_DISK,
        commit_hexsha="abc123",
        path_at_commit=Path("a.py"),
        current_path=Path("a.py"),
        cancel_token=CancelToken(),
        adapter_factory=lambda _path: _FakeAdapter(behavior="succeed", result=expected),
    )

    succeeded, errors, finished = _run_worker(worker)

    assert succeeded == [expected]
    assert errors == []
    assert len(finished) == 1


def test_mode_against_disk_emits_error_on_adapter_exception(qapp) -> None:
    worker = FileHistoryDiffWorker(
        repo_path=Path("/repo"),
        mode=FileHistoryDiffMode.AGAINST_DISK,
        commit_hexsha="abc123",
        path_at_commit=Path("a.py"),
        current_path=Path("a.py"),
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
    marker = tmp_path / "marker"
    worker = FileHistoryDiffWorker(
        repo_path=Path("/repo"),
        mode=FileHistoryDiffMode.AGAINST_DISK,
        commit_hexsha="abc123",
        path_at_commit=Path("a.py"),
        current_path=Path("a.py"),
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
    worker = FileHistoryDiffWorker(
        repo_path=Path("/repo"),
        mode=FileHistoryDiffMode.AGAINST_DISK,
        commit_hexsha="abc123",
        path_at_commit=Path("a.py"),
        current_path=Path("a.py"),
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
