import re
from pathlib import Path

import pytest

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.core.domain.repository import BranchStatus
from local_changes_viewer.core.infra.github_client import GitHubError
from local_changes_viewer.core.services.workspace_scanner_service import (
    WorkspaceScannerService,
)


class FakeFileSystemScanner:
    def __init__(self, repo_paths: list[Path]) -> None:
        self._repo_paths = repo_paths

    def find_git_repos(self, root: Path) -> list[Path]:
        return self._repo_paths


class FakeGitRepoAdapter:
    def __init__(
        self,
        repo_path: Path,
        changes: list[FileChange],
        branch_status: BranchStatus,
        remote_url: str | None = None,
        worktrees: list[Path] | None = None,
    ) -> None:
        self.repo_path = repo_path
        self._changes = changes
        self._branch_status = branch_status
        self._remote_url = remote_url
        self._worktrees = worktrees or []

    def list_changes(self) -> list[FileChange]:
        return self._changes

    def get_branch_status(self) -> BranchStatus:
        return self._branch_status

    def get_remote_url(self, name: str = "origin") -> str | None:
        return self._remote_url

    def list_worktrees(self) -> list[Path]:
        return self._worktrees


class FakeGitHubClient:
    def find_pull_request(self, owner: str, repo: str, branch: str):
        raise GitHubError("GitHub API error 404: Not Found")


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


def test_scan_includes_linked_worktrees_as_separate_repos(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    worktree = tmp_path / "repo_a" / ".worktrees" / "feature-x"
    fixtures = {
        repo_a: FakeGitRepoAdapter(
            repo_a,
            [],
            _branch(),
            worktrees=[worktree],
        ),
        worktree: FakeGitRepoAdapter(
            worktree,
            [FileChange(path=Path("f.py"), change_type=ChangeType.MODIFIED)],
            _branch("feature-x"),
        ),
    }

    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: fixtures[path],
    )

    workspace = service.scan(tmp_path)

    assert {r.name for r in workspace.repositories} == {"repo_a", "feature-x"}
    worktree_result = next(r for r in workspace.repositories if r.name == "feature-x")
    assert worktree_result.changes[0].path == Path("f.py")


def test_scan_skips_repo_when_listing_worktrees_fails(tmp_path: Path):
    repo_a = tmp_path / "repo_a"

    class RaisingWorktreeAdapter(FakeGitRepoAdapter):
        def list_worktrees(self) -> list[Path]:
            raise RuntimeError("git worktree failed")

    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: RaisingWorktreeAdapter(repo_a, [], _branch()),
    )

    workspace = service.scan(tmp_path)

    assert {r.name for r in workspace.repositories} == {"repo_a"}


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


def test_scan_reports_progress_for_discovery_and_each_repo(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    fixtures = {
        repo_a: FakeGitRepoAdapter(repo_a, [], _branch()),
        repo_b: FakeGitRepoAdapter(repo_b, [], _branch()),
    }
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a, repo_b]),
        adapter_factory=lambda path: fixtures[path],
    )

    messages: list[str] = []
    service.scan(tmp_path, on_progress=messages.append)

    assert len(messages) == 5
    assert messages[0] == "Discovering git repositories…"
    assert re.fullmatch(
        r"Found 2 repositories in \d+\.\d{2}s — scanning \(up to 2 in parallel\)…",
        messages[1],
    )
    assert re.fullmatch(r"Scanned 1/2: repo_a… \(\d+\.\d{2}s\)", messages[2])
    assert re.fullmatch(r"Scanned 2/2: repo_b… \(\d+\.\d{2}s\)", messages[3])
    assert re.fullmatch(
        r"Scan finished in \d+\.\d{2}s — 2/2 repos scanned", messages[4]
    )


def test_scan_reports_progress_for_empty_workspace(tmp_path: Path):
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([]),
        adapter_factory=lambda path: pytest.fail("should not be called"),
    )

    messages: list[str] = []
    service.scan(tmp_path, on_progress=messages.append)

    assert len(messages) == 2
    assert messages[0] == "Discovering git repositories…"
    assert re.fullmatch(r"No git repositories found \(\d+\.\d{2}s\)", messages[1])


def test_scan_calls_on_repo_ready_for_each_successful_repo_in_order(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    fixtures = {
        repo_a: FakeGitRepoAdapter(repo_a, [], _branch()),
        repo_b: FakeGitRepoAdapter(repo_b, [], _branch()),
    }
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a, repo_b]),
        adapter_factory=lambda path: fixtures[path],
    )

    ready_names: list[str] = []
    service.scan(tmp_path, on_repo_ready=lambda repo: ready_names.append(repo.name))

    assert ready_names == ["repo_a", "repo_b"]


def test_scan_does_not_call_on_repo_ready_for_broken_repo(tmp_path: Path):
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

    ready_names: list[str] = []
    service.scan(tmp_path, on_repo_ready=lambda repo: ready_names.append(repo.name))

    assert ready_names == ["repo_a"]


def test_scan_routes_github_pr_fetch_error_through_on_log_not_an_exception(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a, [], _branch(), remote_url="git@github.com:getexpain/repo_a.git"
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )

    messages: list[str] = []
    workspace = service.scan(
        tmp_path, github_client=FakeGitHubClient(), on_log=messages.append
    )

    assert workspace.repositories[0].pull_request is None
    assert any("Failed to fetch GitHub PR status for getexpain/repo_a" in m for m in messages)


class RecordingGitHubClient:
    def __init__(self, pull_request: PullRequestInfo | None = None) -> None:
        self.pull_request = pull_request
        self.calls: list[tuple[str, str, str]] = []

    def find_pull_request(self, owner: str, repo: str, branch: str):
        self.calls.append((owner, repo, branch))
        return self.pull_request


def _pr(state: str, repository: str = "getexpain/repo_a") -> PullRequestInfo:
    return PullRequestInfo(
        number=1,
        title="Some PR",
        state=state,
        url="https://github.com/getexpain/repo_a/pull/1",
        comment_count=0,
        review_comment_count=0,
        repository=repository,
    )


def test_scan_reuses_cached_pr_for_terminal_state_and_unchanged_branch(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a, [], _branch("main"), remote_url="git@github.com:getexpain/repo_a.git"
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )
    github_client = RecordingGitHubClient()
    cached_pr = _pr("merged")

    workspace = service.scan(
        tmp_path,
        github_client=github_client,
        previous_pull_requests={repo_a: (cached_pr, "main")},
    )

    assert github_client.calls == []
    assert workspace.repositories[0].pull_request is cached_pr


def test_scan_still_fetches_when_previous_pr_is_open(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a, [], _branch("main"), remote_url="git@github.com:getexpain/repo_a.git"
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )
    fresh_pr = _pr("open")
    github_client = RecordingGitHubClient(pull_request=fresh_pr)
    previous_open_pr = _pr("open")

    workspace = service.scan(
        tmp_path,
        github_client=github_client,
        previous_pull_requests={repo_a: (previous_open_pr, "main")},
    )

    assert github_client.calls == [("getexpain", "repo_a", "main")]
    assert workspace.repositories[0].pull_request is fresh_pr


def test_scan_still_fetches_when_branch_changed(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a, [], _branch("feature-2"), remote_url="git@github.com:getexpain/repo_a.git"
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )
    fresh_pr = _pr("open")
    github_client = RecordingGitHubClient(pull_request=fresh_pr)
    cached_pr = _pr("merged")

    workspace = service.scan(
        tmp_path,
        github_client=github_client,
        previous_pull_requests={repo_a: (cached_pr, "feature-1")},
    )

    assert github_client.calls == [("getexpain", "repo_a", "feature-2")]
    assert workspace.repositories[0].pull_request is fresh_pr


def test_scan_always_fetches_when_no_previous_pull_requests_given(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a, [], _branch("main"), remote_url="git@github.com:getexpain/repo_a.git"
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )
    fresh_pr = _pr("merged")
    github_client = RecordingGitHubClient(pull_request=fresh_pr)

    workspace = service.scan(
        tmp_path,
        github_client=github_client,
        previous_pull_requests=None,
    )

    assert github_client.calls == [("getexpain", "repo_a", "main")]
    assert workspace.repositories[0].pull_request is fresh_pr
