import subprocess
from pathlib import Path

import pytest

from local_changes_viewer.core.services.worktree_terminal_service import (
    WorktreeTerminalError,
    start_worktree_process,
    stop_worktree_process,
)


class FakeRunner:
    def __init__(self, results: list[subprocess.CompletedProcess]) -> None:
        self._results = list(results)
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(args)
        return self._results.pop(0)


def _ok(stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _fail(stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def test_start_worktree_process_returns_window_id_and_embeds_start_command():
    runner = FakeRunner([_ok("42\n")])

    window_id = start_worktree_process(Path("/tmp/my worktree"), runner=runner)

    assert window_id == 42
    [args] = runner.calls
    assert args[0] == "osascript"
    script = " ".join(args)
    assert "nvm use && npm install && npm start" in script
    assert "/tmp/my worktree" in script


def test_start_worktree_process_raises_on_failure():
    runner = FakeRunner([_fail("Terminal got an error")])

    with pytest.raises(WorktreeTerminalError):
        start_worktree_process(Path("/tmp/repo"), runner=runner)


def test_stop_worktree_process_signals_processes_on_the_window_tty():
    runner = FakeRunner([_ok("/dev/ttys004\n"), _ok("")])

    stop_worktree_process(42, runner=runner)

    tty_call, pkill_call = runner.calls
    assert "42" in " ".join(tty_call)
    assert pkill_call == ["pkill", "-9", "-t", "ttys004"]


def test_stop_worktree_process_noops_when_window_already_closed():
    runner = FakeRunner([_fail("Terminal got an error: window id 42 doesn't exist.")])

    stop_worktree_process(42, runner=runner)

    assert len(runner.calls) == 1
