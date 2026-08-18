"""Domain types for File History: pick a tracked file, then browse its commits.

Path-space rule, applied to every `Path` in this module: they are all
repo-relative. Absolute paths exist only at the GUI edge (a results list
displaying them) and where the adapter reads bytes off disk (joining
`repo_path`). Mixing the two is silent, not loud -- `git cat-file -p
<sha>:<abs/path>` fails with a message about the object, not about the path,
and `--follow --name-status` always answers in repo-relative paths, so a
fallback that substitutes an absolute path here puts two incompatible path
spaces in one field with no exception to catch it.
"""

from dataclasses import dataclass, field
from pathlib import Path

from local_changes_viewer.core.domain.commit_log_entry import CommitLogEntry
from local_changes_viewer.core.domain.file_change import ChangeType


@dataclass(frozen=True)
class TrackedFile:
    """One tracked file under a searched subtree.

    Single relative `path`, matching `FileChange` (the one existing domain
    object of this shape) -- every consumer derives the absolute form at the
    edge as `repo_path / path` rather than this type carrying both forms.
    """

    path: Path
    has_local_changes: bool


@dataclass(frozen=True)
class TrackedFilesResult:
    # `too_large=True` means the subtree exceeded the file-count cap and
    # `files` is deliberately left empty -- the caller shows the cap message
    # instead of a (possibly huge, possibly slow-to-produce) list.
    files: list[TrackedFile] = field(default_factory=list)
    too_large: bool = False


@dataclass(frozen=True)
class FileHistoryCommit:
    commit: CommitLogEntry
    path_at_commit: Path
    change_type: ChangeType
    renamed_from: Path | None = None


@dataclass(frozen=True)
class FileHistoryResult:
    entries: list[FileHistoryCommit] = field(default_factory=list)
    # Computed once, by the adapter, from the commit history alone -- never a
    # live filesystem check -- so the GUI never has to re-derive "where does
    # this file live now" from raw status codes itself. `None` means the
    # file is deleted as of its newest listed commit. Mode B still re-checks
    # `current_path.exists()` at diff time, since disk state can change
    # between loading history and clicking a commit.
    current_path: Path | None = None
