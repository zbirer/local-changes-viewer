from datetime import datetime
from pathlib import Path

_ENTRIES: list[str] = []

LOG_FILE_PATH = Path.home() / "Library" / "Logs" / "local-changes-viewer" / "app.log"
LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
_LOG_FILE = LOG_FILE_PATH.open("a")


def log(message: str) -> None:
    entry = f"{datetime.now().isoformat(timespec='milliseconds')}  {message}"
    _ENTRIES.append(entry)
    _LOG_FILE.write(entry + "\n")
    _LOG_FILE.flush()


def all_entries() -> list[str]:
    return list(_ENTRIES)
