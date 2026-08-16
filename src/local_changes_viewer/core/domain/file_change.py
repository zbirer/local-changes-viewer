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
    is_unpushed_commit: bool = False
    commit_message: str | None = None


@dataclass(frozen=True)
class PatchFileDiff:
    """One file's slice of a multi-file patch -- `PatchService.split_patch()`'s
    result shape. `diff_text` is the complete per-file chunk verbatim,
    starting at its `diff --git` line and ending just before the next file's
    (or end of patch): the exact text a single-file parser like
    `GitRepoAdapter.parse_unified_diff` expects, so callers (e.g.
    `StashesDialog`) can render one file's diff without re-splitting the
    whole patch themselves.
    """

    path: Path
    change_type: ChangeType
    diff_text: str
