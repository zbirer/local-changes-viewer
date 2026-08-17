import faulthandler
import io
import sys
from pathlib import Path

import pytest

from local_changes_viewer import main as main_module
from local_changes_viewer.gui import applog


@pytest.fixture(autouse=True)
def _restore_excepthook():
    original = sys.excepthook
    yield
    sys.excepthook = original


def test_enable_crash_diagnostics_enables_faulthandler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crash_log = tmp_path / "crash.log"
    monkeypatch.setattr(main_module, "CRASH_LOG_PATH", crash_log)
    monkeypatch.setattr(faulthandler, "enable", lambda **kwargs: recorded.update(kwargs))
    recorded: dict = {}

    main_module._enable_crash_diagnostics()

    assert recorded["all_threads"] is True
    assert recorded["file"].name == str(crash_log)


def test_enable_crash_diagnostics_writes_timestamped_banner_to_log_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression coverage for the crash-log-only, no-terminal-output bug:
    a segfault used to leave nothing but "segmentation fault" in the user's
    terminal, with all detail landing silently in a single ever-growing
    crash.log. The banner alone doesn't fix that (see the TTY test below),
    but it is what makes the 63 KB file's successive crashes tellable apart
    once diagnostics does land there.
    """
    crash_log = tmp_path / "crash.log"
    monkeypatch.setattr(main_module, "CRASH_LOG_PATH", crash_log)
    monkeypatch.setattr(faulthandler, "enable", lambda **kwargs: None)

    main_module._enable_crash_diagnostics()

    assert crash_log.read_text().strip().startswith("-----")


def test_enable_crash_diagnostics_prefers_stderr_on_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: faulthandler used to always target CRASH_LOG_PATH,
    so launching from a terminal showed nothing but "segmentation fault" --
    the Python stack trace was silently going to the log file instead of
    the terminal the user was actually watching. On a TTY it must go to
    stderr instead, and the terminal should be told where the log file is.
    """
    crash_log = tmp_path / "crash.log"
    monkeypatch.setattr(main_module, "CRASH_LOG_PATH", crash_log)
    recorded: dict = {}
    monkeypatch.setattr(faulthandler, "enable", lambda **kwargs: recorded.update(kwargs))

    fake_stderr = io.StringIO()
    fake_stderr.isatty = lambda: True
    monkeypatch.setattr(sys, "stderr", fake_stderr)

    main_module._enable_crash_diagnostics()

    assert recorded["file"] is fake_stderr
    assert str(crash_log) in fake_stderr.getvalue()


def test_enable_crash_diagnostics_uses_crash_log_file_off_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Companion to the TTY test above: a `.app` bundle launch (or any run
    with no attached terminal) has no one to read stderr, so diagnostics
    must keep going to CRASH_LOG_PATH -- the only place they would
    otherwise ever be found.
    """
    crash_log = tmp_path / "crash.log"
    monkeypatch.setattr(main_module, "CRASH_LOG_PATH", crash_log)
    recorded: dict = {}
    monkeypatch.setattr(faulthandler, "enable", lambda **kwargs: recorded.update(kwargs))

    fake_stderr = io.StringIO()
    fake_stderr.isatty = lambda: False
    monkeypatch.setattr(sys, "stderr", fake_stderr)

    main_module._enable_crash_diagnostics()

    assert recorded["file"].name == str(crash_log)


def test_uncaught_exception_hook_logs_to_applog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main_module, "CRASH_LOG_PATH", tmp_path / "crash.log")
    logged: list[tuple[str, applog.LogLevel]] = []
    monkeypatch.setattr(applog, "log", lambda message, level: logged.append((message, level)))
    called_default_hook: list[tuple] = []
    monkeypatch.setattr(sys, "__excepthook__", lambda *args: called_default_hook.append(args))

    main_module._enable_crash_diagnostics()
    try:
        raise ValueError("boom")
    except ValueError:
        sys.excepthook(*sys.exc_info())

    assert len(logged) == 1
    message, level = logged[0]
    assert "boom" in message
    assert level == applog.LogLevel.ERROR
    assert len(called_default_hook) == 1
