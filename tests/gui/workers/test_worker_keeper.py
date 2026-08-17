import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
import weakref

import pytest
from PySide6.QtCore import QObject, QRunnable, Signal
from PySide6.QtWidgets import QApplication

from local_changes_viewer.gui.workers.worker_keeper import WorkerKeeper


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _FakeSignals(QObject):
    finished = Signal()


class _FakeWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = _FakeSignals()


class _RecordingPool:
    """Stands in for QThreadPool -- records `start()` calls instead of
    running them on a real thread, so a test can fire `finished` by hand
    and inspect WorkerKeeper's state at each step instead of racing a
    genuine worker thread.
    """

    def __init__(self) -> None:
        self.started: list[QRunnable] = []

    def start(self, runnable: QRunnable) -> None:
        self.started.append(runnable)


def test_start_disables_auto_delete_and_starts_on_the_given_pool() -> None:
    """Regression test: QRunnable.setAutoDelete defaults to True, which is
    exactly the bug WorkerKeeper exists to fix -- Qt would otherwise free
    the worker (and drop the last Python ref to its `signals`) on the
    WORKER thread the instant run() returns, racing any cross-thread emit
    from run() that is still a queued event on the main thread.
    """
    keeper = WorkerKeeper()
    worker = _FakeWorker()
    pool = _RecordingPool()

    keeper.start(pool, worker)

    assert worker.autoDelete() is False
    assert pool.started == [worker]


def test_worker_stays_referenced_until_finished_fires(qapp) -> None:
    """The core lifetime guarantee: a caller that starts a worker and then
    drops its own local reference (the normal fire-and-forget call-site
    shape in main_window.py/*_dialog.py) must not lose the worker until
    its `finished` signal has actually been delivered.
    """
    keeper = WorkerKeeper()
    worker = _FakeWorker()
    pool = _RecordingPool()
    worker_ref = weakref.ref(worker)

    keeper.start(pool, worker)
    # Only WorkerKeeper's own reference should be able to keep `worker`
    # alive from here on -- clear the fake pool's bookkeeping list so it
    # doesn't accidentally do the job instead (a real QThreadPool holds no
    # such Python-level list once autoDelete is False).
    pool.started.clear()
    del worker
    gc.collect()

    # Still alive: WorkerKeeper holds the only remaining strong reference.
    assert worker_ref() is not None

    worker_ref().signals.finished.emit()
    gc.collect()

    assert worker_ref() is None


def test_finished_releases_only_the_worker_that_emitted_it(qapp) -> None:
    """Two workers started on the same keeper must not cross-release --
    `self.sender()` inside the finished handler must resolve back to the
    specific worker whose `signals` object emitted, not "whichever worker
    happens to be in the dict".
    """
    keeper = WorkerKeeper()
    pool = _RecordingPool()
    worker_a = _FakeWorker()
    worker_b = _FakeWorker()

    keeper.start(pool, worker_a)
    keeper.start(pool, worker_b)
    assert len(keeper._live) == 2

    worker_a.signals.finished.emit()

    assert len(keeper._live) == 1
    assert worker_b in keeper._live.values()
    assert worker_a not in keeper._live.values()
