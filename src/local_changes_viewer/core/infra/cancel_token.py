"""A cancellable subprocess runner for git calls issued off the GUI thread.

Every other raw-subprocess git call in this app is a private method on
`GitRepoAdapter` (`_run_git_apply`, `_diff_new_file`, `_ls_remote_default_branch`)
-- this is the first one promoted to a standalone module, and the promotion
is deliberate rather than a style choice: a `CancelToken` is constructed on
the GUI thread when a File History request starts, handed across the thread
boundary to a `QRunnable`, and later cancelled from the GUI thread again --
a thread that holds no `GitRepoAdapter` instance of its own. Adapter-private
state can't be reached from there, so this has to live outside the adapter.
"""

import subprocess
import threading
from pathlib import Path


class GitCancelled(Exception):
    """Raised by `CancelToken.run()` when `cancel()` fired before or during the call.

    Distinguishing this from an ordinary git failure matters at exactly one
    call site: every File History worker's `run()` must catch this *before*
    its catch-all `except Exception` handler and swallow it silently (no
    `error` signal) -- `GitCancelled` is itself an `Exception` subclass, so
    handler ordering, not the cancel mechanism, is what actually decides
    whether a routine cancellation gets mistakenly reported as a failure.
    """


class CancelToken:
    """One-shot cancellation handle for a single cancellable git call.

    `cancel()` only kills the in-flight subprocess and flips a flag -- it
    never frees or drops anything QRunnable-owned. That split is the whole
    point: a worker whose subprocess was killed still finishes `run()`
    normally and still emits `finished`, so nothing is ever pulled out from
    under `QThreadPool` mid-flight (the segfault this branch exists to fix).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._process: subprocess.Popen | None = None

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            process = self._process
        # kill() outside the lock: it can block briefly on the OS call, and
        # holding the lock here would stall a concurrent run() that's
        # mid-spawn, waiting on this same lock to store its own process.
        if process is not None and process.poll() is None:
            process.kill()

    def run(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess:
        """Runs `args` as a subprocess, registering it for `cancel()` first.

        Spawn and store happen under the same lock `cancel()` takes -- a gap
        between the two would let a `cancel()` land in between, find no
        process to kill, and let the (now orphaned, but not actually
        cancelled) subprocess run to completion anyway.

        Captures stdout/stderr as raw bytes rather than decoded text: some
        callers (git log, rev-parse) want text, others (git cat-file on a
        possibly-binary blob) need the exact bytes to NUL-sniff before
        deciding whether to decode at all -- decoding here would already
        have destroyed that distinction for the second group.
        """
        with self._lock:
            if self._cancelled:
                raise GitCancelled()
            process = subprocess.Popen(
                args,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
            self._process = process

        stdout, stderr = process.communicate()

        with self._lock:
            if self._cancelled:
                raise GitCancelled()

        return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
