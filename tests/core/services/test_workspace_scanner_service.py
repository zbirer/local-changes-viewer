from pathlib import Path

import pytest

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus
from local_changes_viewer.core.services.workspace_scanner_service import (
    WorkspaceScannerService,
)


class FakeFileSystemScanner:
    def __init__(self, repo_paths: list[Path]) -> None:
        self._repo_paths = repo_paths

    def find_git_repos(self, root: Path) -> list[Path]:
        return self._repo_paths


class FakeGitRepoAdapter:
    def __init__(self, repo_path: Path, changes: list[FileChange], branch_status: BranchStatus) -> None:
        self.repo_path = repo_path
        self._changes = changes
        self._branch_status = branch_status

    def list_changes(self) -> list[FileChange]:
        return self._changes

    def get_branch_status(self) -> BranchStatus:
        return self._branch_status


def _branch(name="main", ahead=0, behind=0) -> BranchStatus:
    return BranchStatus(branch_name=name, ahead=ahead, behind=behind)


def test_scan_builds_workspace_from_multiple_repos(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    fixtures = {
        repo_a: FakeGitRepoAdapter(
            repo_a, [FileChange(path=Path("f1.py"), change_type=ChangeType.MODIFIED)], _branch()
        ),
        repo_b: FakeGitRepoAdapter(
            repo_b, [FileChange(path=Path("f2.py"), change_type=ChangeType.ADDED)], _branch("dev", 1, 2)
        ),
    }

    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a, repo_b]),
        adapter_factory=lambda path: fixtures[path],
    )

    workspace = service.scan(tmp_path)

    assert workspace.root_path == tmp_path
    assert {r.name for r in workspace.repositories} == {"repo_a", "repo_b"}
    repo_b_result = next(r for r in workspace.repositories if r.name == "repo_b")
    assert repo_b_result.branch_status.ahead == 1
    assert repo_b_result.branch_status.behind == 2


def test_scan_filters_ignored_files_by_default(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a,
        [
            FileChange(path=Path("kept.py"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("ignored.log"), change_type=ChangeType.IGNORED),
        ],
        _branch(),
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )

    workspace = service.scan(tmp_path)

    paths = {c.path for c in workspace.repositories[0].changes}
    assert paths == {Path("kept.py")}


def test_scan_includes_ignored_files_when_requested(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a,
        [
            FileChange(path=Path("kept.py"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("ignored.log"), change_type=ChangeType.IGNORED),
        ],
        _branch(),
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )

    workspace = service.scan(tmp_path, include_ignored=True)

    paths = {c.path for c in workspace.repositories[0].changes}
    assert paths == {Path("kept.py"), Path("ignored.log")}


def test_scan_skips_repo_that_fails_to_read(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    repo_broken = tmp_path / "repo_broken"

    class BrokenAdapter:
        def list_changes(self):
            raise RuntimeError("corrupt repo")

        def get_branch_status(self):
            raise RuntimeError("corrupt repo")

    good_adapter = FakeGitRepoAdapter(repo_a, [], _branch())
    factory = {repo_a: good_adapter, repo_broken: BrokenAdapter()}

    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a, repo_broken]),
        adapter_factory=lambda path: factory[path],
    )

    workspace = service.scan(tmp_path)

    assert {r.name for r in workspace.repositories} == {"repo_a"}


def test_scan_returns_empty_workspace_when_no_repos_found(tmp_path: Path):
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([]),
        adapter_factory=lambda path: pytest.fail("should not be called"),
    )

    workspace = service.scan(tmp_path)

    assert workspace.repositories == []
