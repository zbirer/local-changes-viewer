from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StashEntry:
    ref: str
    message: str
    created_at: datetime | None
    author: str
