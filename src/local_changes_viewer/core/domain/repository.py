from dataclasses import dataclass, field
from pathlib import Path

from local_changes_viewer.core.domain.file_change import FileChange


@dataclass(frozen=True)
class BranchStatus:
    branch_name: str
    ahead: int
    behind: int


@dataclass
class Repository:
    path: Path
    name: str
    branch_status: BranchStatus
    changes: list[FileChange] = field(default_factory=list)
