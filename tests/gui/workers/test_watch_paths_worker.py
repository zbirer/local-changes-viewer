import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from local_changes_viewer.gui.workers import watch_paths_worker as watch_paths_worker_module
from local_changes_viewer.gui.workers.watch_paths_worker import WatchPathsWorker


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_collect_watch_paths_failure_emits_error_instead_of_raising(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: run() called collect_watch_paths() with no
    try/except at all, and WatchPathsWorkerSignals defined no `error` signal
    -- a filesystem error (permission denied, vanished repo dir, etc.)
    raised straight out of run() with nothing for the caller to observe.
    """

    def _raise(_repo_paths):
        raise OSError("permission denied")

    monkeypatch.setattr(watch_paths_worker_module, "collect_watch_paths", _raise)

    worker = WatchPathsWorker([])
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


def test_success_path_still_emits_finished_with_the_collected_paths(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    collected = [Path("/repo/a"), Path("/repo/b")]
    monkeypatch.setattr(
        watch_paths_worker_module, "collect_watch_paths", lambda repo_paths: collected
    )

    worker = WatchPathsWorker([Path("/repo")])
    succeeded: list = []
    worker.signals.succeeded.connect(lambda paths: succeeded.append(paths))

    worker.run()

    assert succeeded == [collected]
