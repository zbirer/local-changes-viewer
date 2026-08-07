import json
from pathlib import Path

import pytest

from local_changes_viewer.core.domain.diff import (
    DiffHunk,
    DiffLine,
    DiffLineKind,
    DiffResult,
)
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.services import workspace_cache


@pytest.fixture(autouse=True)
def _redirect_cache_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        workspace_cache, "_CACHE_FILE_PATH", tmp_path / "cache" / "workspace_cache.json"
    )


def _branch(name="main", ahead=0, behind=0) -> BranchStatus:
    return BranchStatus(branch_name=name, ahead=ahead, behind=behind)


def _pr(number=1) -> PullRequestInfo:
    return PullRequestInfo(
        number=number,
        title="Some PR",
        state="open",
        url="https://github.com/getexpain/repo_a/pull/1",
        comment_count=2,
        review_comment_count=3,
        repository="getexpain/repo_a",
        approved=True,
        unresolved_review_thread_count=1,
        last_reviewer="reviewer",
        last_reviewed_at="2026-01-01T00:00:00Z",
        changed_files=5,
        checks_state="SUCCESS",
    )


def test_round_trip_preserves_workspace_with_multiple_repos_and_changes(tmp_path: Path):
    repo_a = Repository(
        path=tmp_path / "repo_a",
        name="repo_a",
        branch_status=_branch("main", ahead=1, behind=2),
        changes=[
            FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("b.py"), change_type=ChangeType.ADDED),
            FileChange(
                path=Path("c.py"),
                old_path=Path("old_c.py"),
                change_type=ChangeType.RENAMED,
            ),
            FileChange(path=Path("d.py"), change_type=ChangeType.DELETED),
        ],
        pull_request=_pr(),
        logical_parent_path=None,
    )
    repo_b = Repository(
        path=tmp_path / "repo_b",
        name="repo_b",
        branch_status=_branch("feature-x"),
        changes=[FileChange(path=Path("e.py"), change_type=ChangeType.UNTRACKED)],
        pull_request=None,
        logical_parent_path=tmp_path / "repo_a",
    )
    workspace = Workspace(root_path=tmp_path, repositories=[repo_a, repo_b])

    workspace_cache.save_workspace(workspace)
    loaded = workspace_cache.load_workspace()

    assert loaded == workspace


def test_diff_is_dropped_on_save_and_loads_back_as_none(tmp_path: Path):
    diff = DiffResult(
        old_ref="HEAD",
        new_ref="working",
        hunks=[
            DiffHunk(
                old_start=1,
                old_count=1,
                new_start=1,
                new_count=1,
                lines=[
                    DiffLine(
                        kind=DiffLineKind.ADDED, old_lineno=None, new_lineno=1, text="x"
                    )
                ],
            )
        ],
    )
    change = FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED, diff=diff)
    repo = Repository(
        path=tmp_path / "repo_a", name="repo_a", branch_status=_branch(), changes=[change]
    )
    workspace = Workspace(root_path=tmp_path, repositories=[repo])

    workspace_cache.save_workspace(workspace)
    loaded = workspace_cache.load_workspace()

    assert loaded is not None
    assert loaded.repositories[0].changes[0].diff is None


def test_load_workspace_returns_none_when_file_does_not_exist(tmp_path: Path):
    assert workspace_cache.load_workspace() is None


def test_load_workspace_returns_none_on_malformed_json(tmp_path: Path):
    workspace_cache._CACHE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    workspace_cache._CACHE_FILE_PATH.write_text("{not valid json")

    assert workspace_cache.load_workspace() is None


def test_load_workspace_returns_none_on_version_mismatch(tmp_path: Path):
    workspace = Workspace(root_path=tmp_path, repositories=[])
    workspace_cache.save_workspace(workspace)
    data = json.loads(workspace_cache._CACHE_FILE_PATH.read_text())
    data["version"] = data["version"] + 1
    workspace_cache._CACHE_FILE_PATH.write_text(json.dumps(data))

    assert workspace_cache.load_workspace() is None


def test_save_workspace_swallows_errors_when_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def _raise(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _raise)
    workspace = Workspace(root_path=tmp_path, repositories=[])

    workspace_cache.save_workspace(workspace)  # must not raise

    assert not workspace_cache._CACHE_FILE_PATH.exists()
