from pathlib import Path

from local_changes_viewer.core.domain.diff import (
    DiffHunk,
    DiffLine,
    DiffLineKind,
    DiffResult,
)
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.domain.workspace import Workspace


def test_diff_line_construction():
    line = DiffLine(kind=DiffLineKind.ADDED, old_lineno=None, new_lineno=5, text="hello")
    assert line.kind is DiffLineKind.ADDED
    assert line.old_lineno is None
    assert line.new_lineno == 5
    assert line.text == "hello"


def test_diff_hunk_holds_lines():
    line = DiffLine(kind=DiffLineKind.CONTEXT, old_lineno=1, new_lineno=1, text="unchanged")
    hunk = DiffHunk(old_start=1, old_count=1, new_start=1, new_count=1, lines=[line])
    assert hunk.lines == [line]


def test_diff_result_holds_hunks():
    hunk = DiffHunk(old_start=1, old_count=0, new_start=1, new_count=1, lines=[])
    result = DiffResult(old_ref="abc123", new_ref="def456", hunks=[hunk])
    assert result.old_ref == "abc123"
    assert result.hunks == [hunk]


def test_diff_result_defaults_to_empty_hunks():
    result = DiffResult(old_ref="abc123", new_ref="def456")
    assert result.hunks == []


def test_file_change_defaults():
    change = FileChange(path=Path("src/foo.py"), change_type=ChangeType.MODIFIED)
    assert change.old_path is None
    assert change.diff is None


def test_file_change_renamed_has_old_path():
    change = FileChange(
        path=Path("src/new_name.py"),
        change_type=ChangeType.RENAMED,
        old_path=Path("src/old_name.py"),
    )
    assert change.old_path == Path("src/old_name.py")


def test_repository_defaults_to_empty_changes():
    branch = BranchStatus(branch_name="main", ahead=0, behind=0)
    repo = Repository(path=Path("/repos/dashboard"), name="dashboard", branch_status=branch)
    assert repo.changes == []
    assert repo.branch_status.branch_name == "main"


def test_workspace_defaults_to_empty_repositories():
    workspace = Workspace(root_path=Path("/repos"))
    assert workspace.repositories == []


def test_workspace_holds_repositories():
    branch = BranchStatus(branch_name="main", ahead=1, behind=2)
    repo = Repository(path=Path("/repos/dashboard"), name="dashboard", branch_status=branch)
    workspace = Workspace(root_path=Path("/repos"), repositories=[repo])
    assert workspace.repositories[0].name == "dashboard"
    assert workspace.repositories[0].branch_status.ahead == 1
