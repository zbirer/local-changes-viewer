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

_ENTRIES: list[str] = []
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
    _LOG_FILE.write(entry + "\n")
    _LOG_FILE.flush()


def all_entries() -> list[str]:
    return list(_ENTRIES)
