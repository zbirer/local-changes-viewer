from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from local_changes_viewer.core.domain.diff import DiffResult


class ChangeType(Enum):
    MODIFIED = auto()
    ADDED = auto()
    DELETED = auto()
    RENAMED = auto()
    UNTRACKED = auto()
    IGNORED = auto()


@dataclass
class FileChange:
    path: Path
    change_type: ChangeType
    old_path: Path | None = None
    diff: DiffResult | None = None
    is_directory: bool = False
