"""Fixes the worker-lifetime use-after-free every `gui/workers/*.py` module
now documents: QRunnable's default autoDelete=True frees the runnable (and
drops the last Python reference to its `signals` QObject) the instant run()
returns, ON THE WORKER THREAD. If a cross-thread `emit()` from run() is still
a queued event when that happens, the main thread later delivers it to freed
memory and segfaults. Every worker's `signals.finished` (see each worker's
own module) now fires exactly once, last, via try/finally, so it is a safe
release point: Qt delivers a QObject's queued signals in FIFO order, so by
the time `finished` is handled, every other signal that worker emitted has
already been dispatched.

`start()` is the single place that turns autoDelete off, so callers can
never forget it and reintroduce the bug at a new call site.
"""

from PySide6.QtCore import QObject, QRunnable, QThreadPool


class WorkerKeeper(QObject):
    def __init__(self) -> None:
        super().__init__()
        # Keyed by id(worker.signals), not id(worker): `finished` is
        # delivered as a slot on this QObject, and self.sender() inside
        # that slot returns the QObject that emitted it -- the `signals`
        # instance -- not the QRunnable itself, so that is what we can look
        # the worker back up by.
        self._live: dict[int, QRunnable] = {}

    def start(self, thread_pool: QThreadPool, worker: QRunnable) -> None:
        # False because the default would free `worker` (and its `signals`)
        # on the WORKER thread the instant run() returns -- see module
        # docstring. We free it ourselves below, once we know run() is done
        # AND every signal it queued has already been delivered.
        worker.setAutoDelete(False)
        self._live[id(worker.signals)] = worker
        worker.signals.finished.connect(self._on_worker_finished)
        thread_pool.start(worker)

    def _on_worker_finished(self) -> None:
        # `finished` is emitted from the worker thread, but this slot is a
        # bound method of a QObject that lives on the main thread, so Qt's
        # auto connection type queues the call here rather than running it
        # on the emitting thread -- which is what makes dropping the
        # reference below safe (see module docstring).
        signals = self.sender()
        self._live.pop(id(signals), None)


# One instance for the whole process, not owned by MainWindow or any
# dialog: MainWindow.closeEvent's `waitForDone(3000)` is a bounded wait, so
# a worker doing a blocking git/GitHub call can still be running -- and can
# still emit -- well after the window (or a short-lived dialog like
# StashesDialog/WorktreesDialog) that started it is gone. Tying this
# keeper's lifetime to that window/dialog would let it get collected first,
# reintroducing the exact in-flight-at-shutdown case this module exists to
# fix.
_keeper = WorkerKeeper()


def start_worker(thread_pool: QThreadPool, worker: QRunnable) -> None:
    """Starts `worker` on `thread_pool`, keeping it alive until its
    `finished` signal is delivered. Use this everywhere in place of a bare
    `thread_pool.start(worker)` -- see module docstring for why."""
    _keeper.start(thread_pool, worker)
