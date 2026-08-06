from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CommitLogEntry:
    hexsha: str
    short_hexsha: str
    message: str
    committed_datetime: datetime
