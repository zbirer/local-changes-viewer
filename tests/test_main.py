import faulthandler
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
