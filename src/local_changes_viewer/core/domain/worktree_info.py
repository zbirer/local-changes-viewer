from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    branch_name: str
    last_activity: datetime | None
    has_unpushed_changes: bool
    created_at: datetime | None
