from datetime import datetime

_ENTRIES: list[str] = []


def log(message: str) -> None:
    _ENTRIES.append(f"{datetime.now().isoformat(timespec='milliseconds')}  {message}")


def all_entries() -> list[str]:
    return list(_ENTRIES)
