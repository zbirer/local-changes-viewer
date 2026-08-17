from collections import deque
from datetime import datetime
from enum import IntEnum
from pathlib import Path


class LogLevel(IntEnum):
    ERROR = 0
    WARNING = 1
    INFO = 2
    DEBUG = 3
    VERBOSE = 4


# Single source of truth for every level name, in declaration order. Any UI
# surface offering a choice of log level (the Log Level… dialog,
# SettingsDialog's combo box) must source its list from here rather than
# hardcoding its own -- otherwise a surface can silently omit a level (e.g.
# VERBOSE) that a user already has selected, and instant-apply persistence
# would then rewrite their setting out from under them.
LOG_LEVEL_NAMES = [level.name for level in LogLevel]

_LEVEL_BY_NAME = {level.name: level for level in LogLevel}

# The in-memory log backs the "App Log" menu action (main_window.py's
# _on_copy_app_log, which copies all_entries() to the clipboard), not the
# on-disk log file -- it must stay bounded regardless of how long the app has
# been running, or a long-lived session slowly leaks memory into a list that
# never shrinks. Oldest entries are dropped first once full.
_MAX_IN_MEMORY_ENTRIES = 5000
_ENTRIES: deque[str] = deque(maxlen=_MAX_IN_MEMORY_ENTRIES)

# A second, much smaller index over the same stream, holding only ERROR-level
# entries -- this is what backs the persistent status-bar error indicator and
# ErrorLogDialog. Kept separate from _ENTRIES (rather than filtering it on
# every read) so the indicator/dialog stay cheap regardless of how many
# non-error entries have piled up. ERROR is the lowest LogLevel value, so
# log() below never drops an ERROR entry regardless of the configured level
# -- see the level check there.
_MAX_ERROR_ENTRIES = 200
_ERROR_ENTRIES: deque[str] = deque(maxlen=_MAX_ERROR_ENTRIES)
_current_level = LogLevel.INFO

LOG_FILE_PATH = Path.home() / "Library" / "Logs" / "local-changes-viewer" / "app.log"
LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
_LOG_FILE = LOG_FILE_PATH.open("a")


def level_from_name(name: str) -> LogLevel:
    return _LEVEL_BY_NAME.get(name, LogLevel.INFO)


def set_level(level: LogLevel) -> None:
    global _current_level
    _current_level = level


def get_level() -> LogLevel:
    return _current_level


def log(message: str, level: LogLevel = LogLevel.VERBOSE) -> None:
    if level > _current_level:
        return
    entry = f"{datetime.now().isoformat(timespec='milliseconds')}  [{level.name}]  {message}"
    _ENTRIES.append(entry)
    if level == LogLevel.ERROR:
        _ERROR_ENTRIES.append(entry)
    # applog.log is called from ordinary menu/action handlers throughout the
    # app, not just from a dedicated logging path -- a full disk or a
    # yanked log directory must not turn every such click into an uncaught
    # OSError. The in-memory _ENTRIES list above is the fallback record when
    # the file write itself can't be trusted.
    try:
        _LOG_FILE.write(entry + "\n")
        _LOG_FILE.flush()
    except OSError:
        pass


def all_entries() -> list[str]:
    return list(_ENTRIES)


def recent_errors() -> list[str]:
    # Newest first: this is what both the status-bar tooltip (most recent
    # error only) and ErrorLogDialog (the full list) want, so neither has to
    # re-reverse it themselves.
    return list(reversed(_ERROR_ENTRIES))


def error_count() -> int:
    return len(_ERROR_ENTRIES)


def clear_errors() -> None:
    _ERROR_ENTRIES.clear()
