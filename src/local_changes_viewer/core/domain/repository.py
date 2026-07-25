from dataclasses import dataclass, field
from pathlib import Path

from local_changes_viewer.core.domain.file_change import FileChange
from local_changes_viewer.core.domain.pull_request import PullRequestInfo


@dataclass(frozen=True)
class BranchStatus:
    branch_name: str
    ahead: int
    behind: int
    parent_branch: str | None = None
    default_branch: str | None = None


@dataclass
class Repository:
    path: Path
    name: str
    branch_status: BranchStatus
    changes: list[FileChange] = field(default_factory=list)
    pull_request: PullRequestInfo | None = None
