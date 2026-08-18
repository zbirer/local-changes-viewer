import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from local_changes_viewer.core.infra.cancel_token import CancelToken, GitCancelled


def test_run_returns_completed_process_on_success(tmp_path: Path):
    token = CancelToken()

    result = token.run([sys.executable, "-c", "print('hello')"], cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == b"hello"
    assert token.is_cancelled is False


def test_run_propagates_nonzero_exit(tmp_path: Path):
    token = CancelToken()

    result = token.run([sys.executable, "-c", "import sys; sys.exit(7)"], cwd=tmp_path)

    assert result.returncode == 7


def test_cancel_before_run_raises_git_cancelled_without_spawning(tmp_path: Path):
    token = CancelToken()
    token.cancel()

    with pytest.raises(GitCancelled):
        # A marker file the process would create if it ever actually ran --
        # proves cancel() before run() never spawns anything at all, rather
        # than spawning and immediately killing it.
        token.run(
            [sys.executable, "-c", f"open({str(tmp_path / 'marker')!r}, 'w').close()"],
            cwd=tmp_path,
        )

    assert not (tmp_path / "marker").exists()


def test_is_cancelled_reflects_state():
    token = CancelToken()
    assert token.is_cancelled is False
    token.cancel()
    assert token.is_cancelled is True


def test_cancel_mid_flight_kills_process_and_raises_git_cancelled(tmp_path: Path):
    marker = tmp_path / "started.marker"
    script = tmp_path / "block.py"
    # Writes the marker the instant it starts, then blocks far longer than
    # this test should ever have to wait -- if cancel() actually works, run()
    # returns almost immediately after the marker appears; if cancel() were a
    # no-op, this test would hang for the full 30s instead.
    script.write_text(
        f"import pathlib, time\n"
        f"pathlib.Path({str(marker)!r}).write_text('x')\n"
        f"time.sleep(30)\n"
    )

    token = CancelToken()
    outcome: dict[str, object] = {}

    def _run_in_background() -> None:
        try:
            token.run([sys.executable, str(script)], cwd=tmp_path)
        except GitCancelled as exc:
            outcome["exception"] = exc
        except Exception as exc:  # noqa: BLE001 - captured for the assertion below
            outcome["exception"] = exc
        else:
            outcome["exception"] = None

    worker_thread = threading.Thread(target=_run_in_background)
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
    assert isinstance(outcome.get("exception"), GitCancelled)
