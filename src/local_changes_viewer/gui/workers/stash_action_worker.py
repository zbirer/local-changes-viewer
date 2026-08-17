"""Runs one blocking, subprocess-backed stash mutation (apply/pop/drop/
restore-file) off the GUI thread for StashesDialog's Apply/Pop/"Delete
stash"/"Restore file" actions. Unlike `list_stashes()` -- a single fast
`git stash list` call StashesDialog's own docstring says doesn't need
worker machinery -- these are `git stash apply|pop|drop`/`git checkout --
<path>` subprocess calls that can block for a while, so running them
straight on the GUI thread used to freeze the whole app.

Deliberately one generic worker (an `action` callable) rather than four
near-identical worker classes: every caller already has its adapter method
call fully bound (adapter_factory, repo path, stash ref, and/or file path)
by the time it starts the worker, so there is nothing left to parameterize
per-action beyond "run this and report success or the exception".
"""

from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Signal


class StashActionWorkerSignals(QObject):
    # Renamed from `finished` -- that name is now reserved for the bare
    # signal below that WorkerKeeper waits on to release its reference;
    # this one still only fires on success (both take no arguments, but
    # they are not interchangeable: this one means "action succeeded").
    succeeded = Signal()
    error = Signal(str)
    finished = Signal()


class StashActionWorker(QRunnable):
    def __init__(self, action: Callable[[], None]) -> None:
        super().__init__()
        self._action = action
        self.signals = StashActionWorkerSignals()

    def run(self) -> None:
        try:
            try:
                self._action()
            except Exception as exc:  # noqa: BLE001 - reported via signal, not raised on worker thread
                self.signals.error.emit(str(exc))
            else:
                self.signals.succeeded.emit()
        finally:
            # Last, always: WorkerKeeper frees this worker only once this
            # fires, and queued signals are FIFO, so every emit above is
            # delivered first -- see worker_keeper.py for the full reason.
            self.signals.finished.emit()
