"""Regression tests for applog's two defect fixes:

1. _ENTRIES used to be a plain list that grew for the entire process
   lifetime -- it must now be bounded, dropping the oldest entry first.
2. A write/flush failure on the on-disk log file (e.g. a full disk) used to
   raise straight out of applog.log(), and applog.log() is called from
   ordinary menu/action handlers throughout the app -- it must never raise.

No QApplication/offscreen platform is needed here: applog.py has no PySide
imports, it's plain Python plus a module-level file handle.
"""

from collections import deque

import pytest

from local_changes_viewer.gui import applog


class _NullWritable:
    def write(self, _data: str) -> int:
        return 0

    def flush(self) -> None:
        return None


class _RaisingWritable:
    def write(self, _data: str) -> int:
        raise OSError("disk full")

    def flush(self) -> None:
        raise OSError("disk full")


def _entry_message(entry: str) -> str:
    # log() formats entries as "<iso-timestamp>  [<LEVEL>]  <message>".
    return entry.split("  ", 2)[-1]


def test_in_memory_entries_are_bounded_and_drop_oldest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(applog, "_ENTRIES", deque(maxlen=3))
    monkeypatch.setattr(applog, "_LOG_FILE", _NullWritable())

    for i in range(5):
        applog.log(f"entry {i}", level=applog.LogLevel.ERROR)

    entries = applog.all_entries()
    assert len(entries) == 3
    assert [_entry_message(e) for e in entries] == ["entry 2", "entry 3", "entry 4"]


def test_log_never_raises_when_the_log_file_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(applog, "_ENTRIES", deque(maxlen=applog._MAX_IN_MEMORY_ENTRIES))
    monkeypatch.setattr(applog, "_LOG_FILE", _RaisingWritable())

    # Must not raise even though the underlying file object raises on every
    # write/flush call.
    applog.log("should survive a full disk", level=applog.LogLevel.ERROR)

    assert _entry_message(applog.all_entries()[-1]) == "should survive a full disk"


def test_log_never_raises_when_only_flush_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """write() succeeding but flush() failing (a plausible partial-failure
    mode for a nearly-full disk) must be swallowed the same way."""

    class _WriteOkFlushFails:
        def write(self, _data: str) -> int:
            return 0

        def flush(self) -> None:
            raise OSError("disk full")

    monkeypatch.setattr(applog, "_ENTRIES", deque(maxlen=applog._MAX_IN_MEMORY_ENTRIES))
    monkeypatch.setattr(applog, "_LOG_FILE", _WriteOkFlushFails())

    applog.log("flush failure must not raise", level=applog.LogLevel.ERROR)

    assert _entry_message(applog.all_entries()[-1]) == "flush failure must not raise"


def test_error_log_lands_in_recent_errors_and_bumps_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog, "_ERROR_ENTRIES", deque(maxlen=applog._MAX_ERROR_ENTRIES))
    monkeypatch.setattr(applog, "_LOG_FILE", _NullWritable())

    applog.log("boom", level=applog.LogLevel.ERROR)

    assert applog.error_count() == 1
    assert _entry_message(applog.recent_errors()[0]) == "boom"


def test_non_error_log_does_not_land_in_recent_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog, "_ERROR_ENTRIES", deque(maxlen=applog._MAX_ERROR_ENTRIES))
    monkeypatch.setattr(applog, "_LOG_FILE", _NullWritable())

    applog.log("just a warning", level=applog.LogLevel.WARNING)
    applog.log("just info", level=applog.LogLevel.INFO)

    assert applog.error_count() == 0
    assert applog.recent_errors() == []


def test_clear_errors_empties_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog, "_ERROR_ENTRIES", deque(maxlen=applog._MAX_ERROR_ENTRIES))
    monkeypatch.setattr(applog, "_LOG_FILE", _NullWritable())

    applog.log("boom", level=applog.LogLevel.ERROR)
    assert applog.error_count() == 1

    applog.clear_errors()

    assert applog.error_count() == 0
    assert applog.recent_errors() == []


def test_recent_errors_is_bounded_and_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(applog, "_ERROR_ENTRIES", deque(maxlen=3))
    monkeypatch.setattr(applog, "_LOG_FILE", _NullWritable())

    for i in range(5):
        applog.log(f"error {i}", level=applog.LogLevel.ERROR)

    errors = applog.recent_errors()
    assert applog.error_count() == 3
    assert [_entry_message(e) for e in errors] == ["error 4", "error 3", "error 2"]


def test_error_entries_are_recorded_even_at_the_most_restrictive_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(applog, "_ERROR_ENTRIES", deque(maxlen=applog._MAX_ERROR_ENTRIES))
    monkeypatch.setattr(applog, "_LOG_FILE", _NullWritable())
    # ERROR is LogLevel's lowest value, so configuring the most restrictive
    # level (ERROR itself) must still let an ERROR entry through -- this is
    # the "no separate registration step" guarantee the indicator relies on.
    monkeypatch.setattr(applog, "_current_level", applog.LogLevel.ERROR)

    applog.log("still recorded", level=applog.LogLevel.ERROR)

    assert applog.error_count() == 1
    assert _entry_message(applog.recent_errors()[0]) == "still recorded"
