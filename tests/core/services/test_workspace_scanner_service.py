import re
from pathlib import Path

import pytest

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.core.domain.repository import BranchStatus
from local_changes_viewer.core.infra.github_client import GitHubError
from local_changes_viewer.core.services import workspace_scanner_service as wss
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
        unpushed_changes: list[FileChange] | None = None,
    ) -> None:
        self.repo_path = repo_path
        self._changes = changes
        self._branch_status = branch_status
        self._remote_url = remote_url
        self._worktrees = worktrees or []
        self._unpushed_changes = unpushed_changes or []
        self.list_changes_calls = 0
        self.get_branch_status_calls = 0

    def list_changes(self, include_unpushed_commits: bool = False) -> list[FileChange]:
        self.list_changes_calls += 1
        if include_unpushed_commits:
            return self._changes + self._unpushed_changes
        return self._changes

    def get_branch_status(self) -> BranchStatus:
        self.get_branch_status_calls += 1
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


def test_scan_with_profile_repo_names_only_scans_matching_repos(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    fixtures = {
        repo_a: FakeGitRepoAdapter(
            repo_a, [FileChange(path=Path("f1.py"), change_type=ChangeType.MODIFIED)], _branch()
        ),
        repo_b: FakeGitRepoAdapter(
            repo_b, [FileChange(path=Path("f2.py"), change_type=ChangeType.ADDED)], _branch()
        ),
    }

    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a, repo_b]),
        adapter_factory=lambda path: fixtures[path],
    )

    workspace = service.scan(tmp_path, profile_repo_names={"repo_a"})

    assert {r.name for r in workspace.repositories} == {"repo_a"}


def test_scan_with_profile_repo_names_keeps_worktree_of_matching_parent(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    worktree = tmp_path / "repo_a" / ".worktrees" / "feature-x"
    worktree.mkdir(parents=True)
    fixtures = {
        repo_a: FakeGitRepoAdapter(repo_a, [], _branch(), worktrees=[worktree]),
        repo_b: FakeGitRepoAdapter(repo_b, [], _branch()),
        worktree: FakeGitRepoAdapter(
            worktree,
            [FileChange(path=Path("f.py"), change_type=ChangeType.MODIFIED)],
            _branch("feature-x"),
        ),
    }

    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a, repo_b]),
        adapter_factory=lambda path: fixtures[path],
    )

    workspace = service.scan(tmp_path, profile_repo_names={"repo_a"})

    assert {r.name for r in workspace.repositories} == {"repo_a", "feature-x"}


def test_scan_with_profile_repo_names_skips_github_fetch_for_inherited_worktree(
    tmp_path: Path,
):
    repo_a = tmp_path / "repo_a"
    worktree = tmp_path / "repo_a" / ".worktrees" / "feature-x"
    worktree.mkdir(parents=True)
    fixtures = {
        repo_a: FakeGitRepoAdapter(
            repo_a,
            [],
            _branch(),
            worktrees=[worktree],
            remote_url="git@github.com:getexpain/repo_a.git",
        ),
        worktree: FakeGitRepoAdapter(
            worktree,
            [FileChange(path=Path("f.py"), change_type=ChangeType.MODIFIED)],
            _branch("feature-x"),
            remote_url="git@github.com:getexpain/repo_a.git",
        ),
    }

    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: fixtures[path],
    )
    github_client = RecordingGitHubClient()

    workspace = service.scan(
        tmp_path, github_client=github_client, profile_repo_names={"repo_a"}
    )

    assert github_client.calls == [("getexpain", "repo_a", "main")]
    worktree_result = next(r for r in workspace.repositories if r.name == "feature-x")
    assert worktree_result.pull_request is None


def test_scan_includes_linked_worktrees_as_separate_repos(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    worktree = tmp_path / "repo_a" / ".worktrees" / "feature-x"
    worktree.mkdir(parents=True)
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


def test_scan_records_logical_parent_for_sibling_directory_worktree(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    worktree = tmp_path / "repo_a-worktrees" / "feature-x"
    worktree.mkdir(parents=True)
    fixtures = {
        repo_a: FakeGitRepoAdapter(repo_a, [], _branch(), worktrees=[worktree]),
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

    repo_a_result = next(r for r in workspace.repositories if r.name == "repo_a")
    worktree_result = next(r for r in workspace.repositories if r.name == "feature-x")
    assert worktree_result.logical_parent_path == repo_a
    assert repo_a_result.logical_parent_path is None


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


def test_scan_skips_stale_worktree_paths_that_no_longer_exist(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    stale_worktree = tmp_path / "repo_a-worktrees" / "deleted-feature"
    fixtures = {
        repo_a: FakeGitRepoAdapter(repo_a, [], _branch(), worktrees=[stale_worktree]),
    }

    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: fixtures[path],
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


def test_scan_excludes_unpushed_commits_by_default(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a,
        [FileChange(path=Path("kept.py"), change_type=ChangeType.MODIFIED)],
        _branch(),
        unpushed_changes=[
            FileChange(
                path=Path("unpushed.py"),
                change_type=ChangeType.MODIFIED,
                is_unpushed_commit=True,
            )
        ],
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )

    workspace = service.scan(tmp_path)

    paths = {c.path for c in workspace.repositories[0].changes}
    assert paths == {Path("kept.py")}


def test_scan_includes_unpushed_commits_when_requested(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a,
        [FileChange(path=Path("kept.py"), change_type=ChangeType.MODIFIED)],
        _branch(),
        unpushed_changes=[
            FileChange(
                path=Path("unpushed.py"),
                change_type=ChangeType.MODIFIED,
                is_unpushed_commit=True,
            )
        ],
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )

    workspace = service.scan(tmp_path, include_unpushed_commits=True)

    paths = {c.path for c in workspace.repositories[0].changes}
    assert paths == {Path("kept.py"), Path("unpushed.py")}


def test_scan_skips_repo_that_fails_to_read(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    repo_broken = tmp_path / "repo_broken"

    class BrokenAdapter:
        def list_changes(self, include_unpushed_commits: bool = False):
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

    assert len(messages) == 6
    assert messages[0] == "Discovering git repositories…"
    assert re.fullmatch(
        r"Found 2 repositories in \d+\.\d{2}s — scanning \(up to 2 in parallel\)…",
        messages[1],
    )
    assert re.fullmatch(r"Scanned 1/2: repo_a… \(\d+\.\d{2}s\)", messages[2])
    assert re.fullmatch(r"Scanned 2/2: repo_b… \(\d+\.\d{2}s\)", messages[3])
    assert re.fullmatch(
        r"Repo scan phase done in \d+\.\d{2}s — git \d+\.\d{2}s \(aggregate\), "
        r"GitHub fetch \d+\.\d{2}s \(aggregate\)",
        messages[4],
    )
    assert re.fullmatch(
        r"Scan finished in \d+\.\d{2}s — 2/2 repos scanned", messages[5]
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
        def list_changes(self, include_unpushed_commits: bool = False):
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


def test_scan_reuses_cached_changes_for_repo_not_in_dirty_paths(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    adapter_a = FakeGitRepoAdapter(
        repo_a, [FileChange(path=Path("f1.py"), change_type=ChangeType.MODIFIED)], _branch()
    )
    adapter_b = FakeGitRepoAdapter(
        repo_b, [FileChange(path=Path("f2.py"), change_type=ChangeType.ADDED)], _branch()
    )
    fixtures = {repo_a: adapter_a, repo_b: adapter_b}
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a, repo_b]),
        adapter_factory=lambda path: fixtures[path],
    )

    service.scan(tmp_path)
    assert adapter_a.list_changes_calls == 1
    assert adapter_b.list_changes_calls == 1

    workspace = service.scan(tmp_path, dirty_paths={repo_b})

    assert adapter_a.list_changes_calls == 1
    assert adapter_a.get_branch_status_calls == 1
    assert adapter_b.list_changes_calls == 2
    repo_a_result = next(r for r in workspace.repositories if r.name == "repo_a")
    assert repo_a_result.changes[0].path == Path("f1.py")


def test_scan_rescans_repo_in_dirty_paths(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter_a = FakeGitRepoAdapter(
        repo_a, [FileChange(path=Path("f1.py"), change_type=ChangeType.MODIFIED)], _branch()
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter_a,
    )

    service.scan(tmp_path)
    service.scan(tmp_path, dirty_paths={repo_a})

    assert adapter_a.list_changes_calls == 2
    assert adapter_a.get_branch_status_calls == 2


def test_scan_with_dirty_paths_none_rescans_everything(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter_a = FakeGitRepoAdapter(
        repo_a, [FileChange(path=Path("f1.py"), change_type=ChangeType.MODIFIED)], _branch()
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter_a,
    )

    service.scan(tmp_path)
    service.scan(tmp_path, dirty_paths=None)

    assert adapter_a.list_changes_calls == 2
    assert adapter_a.get_branch_status_calls == 2


def test_scan_reuses_open_pr_within_ttl_window(tmp_path: Path):
    repo_a = tmp_path / "repo_a"
    adapter_a = FakeGitRepoAdapter(
        repo_a, [], _branch("main"), remote_url="git@github.com:getexpain/repo_a.git"
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter_a,
    )
    fresh_pr = _pr("open")
    github_client = RecordingGitHubClient(pull_request=fresh_pr)

    first_workspace = service.scan(tmp_path, github_client=github_client)
    fetched_pr = first_workspace.repositories[0].pull_request

    previous_pull_requests = {repo_a: (fetched_pr, "main")}
    second_workspace = service.scan(
        tmp_path,
        github_client=github_client,
        previous_pull_requests=previous_pull_requests,
    )

    assert github_client.calls == [("getexpain", "repo_a", "main")]
    assert second_workspace.repositories[0].pull_request is fetched_pr


def test_scan_does_not_refetch_pr_within_ttl_when_previous_lookup_found_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regression test for the runaway-refresh bug: a branch with no open PR
    used to be re-queried on *every* scan (the old cached-PR reuse only ever
    applied when a previous PR object existed), which hammered GitHub every
    ~2s while a busy file watcher kept triggering auto-refresh scans."""
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a, [], _branch("main"), remote_url="git@github.com:getexpain/repo_a.git"
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )
    github_client = RecordingGitHubClient(pull_request=None)
    clock = [1_000.0]
    monkeypatch.setattr(wss.time, "monotonic", lambda: clock[0])

    first = service.scan(tmp_path, github_client=github_client)
    assert first.repositories[0].pull_request is None
    assert len(github_client.calls) == 1

    clock[0] += 10.0  # well inside the 60s TTL
    second = service.scan(tmp_path, github_client=github_client)

    assert second.repositories[0].pull_request is None
    assert len(github_client.calls) == 1  # not re-fetched


def test_scan_refetches_pr_once_ttl_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a, [], _branch("main"), remote_url="git@github.com:getexpain/repo_a.git"
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )
    github_client = RecordingGitHubClient(pull_request=None)
    clock = [1_000.0]
    monkeypatch.setattr(wss.time, "monotonic", lambda: clock[0])

    service.scan(tmp_path, github_client=github_client)
    assert len(github_client.calls) == 1

    clock[0] += wss._PR_REFETCH_INTERVAL_SECONDS + 1.0
    service.scan(tmp_path, github_client=github_client)

    assert len(github_client.calls) == 2


def test_scan_refetches_pr_immediately_when_branch_changes_within_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo_a = tmp_path / "repo_a"
    adapter = FakeGitRepoAdapter(
        repo_a, [], _branch("main"), remote_url="git@github.com:getexpain/repo_a.git"
    )
    service = WorkspaceScannerService(
        filesystem_scanner=FakeFileSystemScanner([repo_a]),
        adapter_factory=lambda path: adapter,
    )
    github_client = RecordingGitHubClient(pull_request=None)
    clock = [1_000.0]
    monkeypatch.setattr(wss.time, "monotonic", lambda: clock[0])

    service.scan(tmp_path, github_client=github_client)
    assert len(github_client.calls) == 1

    clock[0] += 1.0  # still well inside the TTL
    adapter._branch_status = _branch("feature-x")
    service.scan(tmp_path, github_client=github_client)

    assert len(github_client.calls) == 2
    assert github_client.calls[1] == ("getexpain", "repo_a", "feature-x")
