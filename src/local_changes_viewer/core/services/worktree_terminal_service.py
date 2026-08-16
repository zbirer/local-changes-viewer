"""Runs a worktree's dev-server startup command in a dedicated Terminal.app window.

Uses AppleScript (via `osascript`) rather than a plain background QProcess so the
user can see `npm install`/`npm start` output live, exactly as if they'd typed the
command themselves. The Terminal window's AppleScript id is the handle callers keep
to later stop that same run.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Callable

_START_COMMAND = "nvm use && npm install && npm start"

Runner = Callable[..., subprocess.CompletedProcess]


class WorktreeTerminalError(RuntimeError):
    pass


def _osascript_lines(*lines: str) -> list[str]:
    args = ["osascript"]
    for line in lines:
        args += ["-e", line]
    return args


def start_worktree_process(path: Path, runner: Runner = subprocess.run) -> int:
    """Opens a new Terminal.app window in `path` running the start command.

    Returns the new window's AppleScript id, which `stop_worktree_process` needs to
    stop this exact run later.
    """
    shell_command = f"cd {shlex.quote(str(path))} && {_START_COMMAND}"
    escaped_command = shell_command.replace("\\", "\\\\").replace('"', '\\"')
    result = runner(
        _osascript_lines(
            'tell application "Terminal"',
            "activate",
            "set newWindow to make new window",
            f'do script "{escaped_command}" in newWindow',
            "return id of newWindow",
            "end tell",
        ),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise WorktreeTerminalError(result.stderr.strip() or "osascript failed")
    return int(result.stdout.strip())


def stop_worktree_process(window_id: int, runner: Runner = subprocess.run) -> None:
    """Kills whatever is running in the Terminal window `window_id`.

    Looks up the window's tty and signals every process attached to it, rather than
    closing the window itself, since closing pops Terminal's "still running" prompt.
    Silently no-ops if the window was already closed by the user.
    """
    tty_result = runner(
        _osascript_lines(
            'tell application "Terminal"',
            f"return tty of (first tab of (first window whose id is {window_id}))",
            "end tell",
        ),
        capture_output=True,
        text=True,
    )
    tty_path = tty_result.stdout.strip()
    if tty_result.returncode != 0 or not tty_path:
        return
    tty_name = tty_path.removeprefix("/dev/")
    runner(["pkill", "-9", "-t", tty_name], capture_output=True, text=True)
