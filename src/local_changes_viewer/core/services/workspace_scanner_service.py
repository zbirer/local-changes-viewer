import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from local_changes_viewer.core.domain.file_change import ChangeType
from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.core.domain.repository import Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.infra.filesystem_scanner import FileSystemScanner
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.core.infra.github_client import (
    GitHubClient,
    GitHubError,
    parse_github_owner_repo,
)

_MAX_PARALLEL_REPO_SCANS = 8


class WorkspaceScannerService:
    def __init__(
        self,
        filesystem_scanner: FileSystemScanner | None = None,
        adapter_factory: Callable[[Path], GitRepoAdapter] | None = None,
    ) -> None:
        self._filesystem_scanner = filesystem_scanner or FileSystemScanner()
        self._adapter_factory = adapter_factory or GitRepoAdapter

    def scan(
        self,
        root: Path,
        include_ignored: bool = False,
        on_progress: Callable[[str], None] | None = None,
        on_repo_ready: Callable[[Repository], None] | None = None,
        github_client: GitHubClient | None = None,
        on_log: Callable[[str], None] | None = None,
        previous_pull_requests: dict[Path, tuple[PullRequestInfo, str]] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        profile_repo_names: set[str] | None = None,
        include_unpushed_commits: bool = False,
    ) -> Workspace:
        on_progress = on_progress or (lambda _message: None)
        on_repo_ready = on_repo_ready or (lambda _repo: None)
        on_log = on_log or (lambda _message: None)
        is_cancelled = is_cancelled or (lambda: False)

        scan_started_at = time.monotonic()
        on_progress("Discovering git repositories…")
        discovery_started_at = time.monotonic()
        repo_paths = self._filesystem_scanner.find_git_repos(root)
        repo_paths, worktree_parents, worktrees_by_parent = self._expand_with_worktrees(
            repo_paths, on_log
        )
        if profile_repo_names is not None:
            repo_paths = [
                path
                for path in repo_paths
                if self._repo_in_profile(path, worktree_parents, profile_repo_names)
            ]
            on_log(
                f"Profile filter active: scanning {len(repo_paths)} of the discovered repositories"
            )
        discovery_seconds = time.monotonic() - discovery_started_at
        total = len(repo_paths)
        if total == 0:
            on_progress(f"No git repositories found ({discovery_seconds:.2f}s)")
            return Workspace(root_path=root, repositories=[])

        on_progress(
            f"Found {total} repositories in {discovery_seconds:.2f}s — scanning "
            f"(up to {min(_MAX_PARALLEL_REPO_SCANS, total)} in parallel)…"
        )
        repositories: list[Repository] = []

        # Each repo scan is I/O-bound (shells out to git), so scanning repos in
        # parallel cuts wall-clock time roughly by the pool size. executor.map
        # yields results in submission order even though work completes out of
        # order, keeping progress messages and results deterministic.
        with ThreadPoolExecutor(max_workers=min(_MAX_PARALLEL_REPO_SCANS, total)) as executor:
            results = executor.map(
                lambda repo_path: self._scan_repo(
                    repo_path,
                    include_ignored,
                    github_client,
                    on_log,
                    (previous_pull_requests or {}).get(repo_path),
                    worktree_parents.get(repo_path),
                    worktrees_by_parent.get(repo_path),
                    is_cancelled,
                    include_unpushed_commits,
                    profile_repo_names is not None and repo_path.name not in profile_repo_names,
                ),
                repo_paths,
            )
            for index, (repo_path, timed_repo) in enumerate(zip(repo_paths, results), start=1):
                if is_cancelled():
                    break
                repo, repo_seconds = timed_repo
                on_progress(
                    f"Scanned {index}/{total}: {repo_path.name}… ({repo_seconds:.2f}s)"
                )
                if repo is not None:
                    repositories.append(repo)
                    on_repo_ready(repo)

        total_seconds = time.monotonic() - scan_started_at
        on_progress(
            f"Scan finished in {total_seconds:.2f}s — {len(repositories)}/{total} repos scanned"
        )

        return Workspace(root_path=root, repositories=repositories)

    def scan_repo(
        self,
        repo_path: Path,
        include_ignored: bool = False,
        github_client: GitHubClient | None = None,
        on_log: Callable[[str], None] | None = None,
        previous_pull_request: tuple[PullRequestInfo, str] | None = None,
        logical_parent_path: Path | None = None,
        include_unpushed_commits: bool = False,
    ) -> Repository | None:
        on_log = on_log or (lambda _message: None)
        try:
            nested_worktree_paths = [
                path
                for path in self._adapter_factory(repo_path).list_worktrees()
                if path.exists()
            ]
        except Exception as exc:
            on_log(f"{repo_path.name}: failed to list worktrees: {exc}")
            nested_worktree_paths = []

        repo, _ = self._scan_repo(
            repo_path,
            include_ignored,
            github_client,
            on_log,
            previous_pull_request,
            logical_parent_path,
            nested_worktree_paths,
            None,
            include_unpushed_commits,
        )
        return repo

    def _expand_with_worktrees(
        self, repo_paths: list[Path], on_log: Callable[[str], None]
    ) -> tuple[list[Path], dict[Path, Path], dict[Path, list[Path]]]:
        expanded = list(repo_paths)
        seen = set(repo_paths)
        worktree_parents: dict[Path, Path] = {}
        worktrees_by_parent: dict[Path, list[Path]] = {}
        for repo_path in repo_paths:
            try:
                worktree_paths = self._adapter_factory(repo_path).list_worktrees()
            except Exception as exc:
                on_log(f"{repo_path.name}: failed to list worktrees: {exc}")
                continue
            for worktree_path in worktree_paths:
                if not worktree_path.exists():
                    on_log(f"{repo_path.name}: skipping stale worktree path {worktree_path}")
                    continue
                worktree_parents.setdefault(worktree_path, repo_path)
                worktrees_by_parent.setdefault(repo_path, []).append(worktree_path)
                if worktree_path not in seen:
                    seen.add(worktree_path)
                    expanded.append(worktree_path)
        return expanded, worktree_parents, worktrees_by_parent

    def _scan_repo(
        self,
        repo_path: Path,
        include_ignored: bool,
        github_client: GitHubClient | None = None,
        on_log: Callable[[str], None] | None = None,
        previous_pull_request: tuple[PullRequestInfo, str] | None = None,
        logical_parent_path: Path | None = None,
        nested_worktree_paths: list[Path] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        include_unpushed_commits: bool = False,
        skip_github_fetch: bool = False,
    ) -> tuple[Repository | None, float]:
        on_log = on_log or (lambda _message: None)
        is_cancelled = is_cancelled or (lambda: False)
        started_at = time.monotonic()
        if is_cancelled():
            return None, 0.0
        try:
            adapter = self._adapter_factory(repo_path)
            changes = adapter.list_changes(include_unpushed_commits=include_unpushed_commits)
            branch_status = adapter.get_branch_status()
        except Exception as exc:
            on_log(f"Skipping repo {repo_path}: failed to read git state: {exc}\n{traceback.format_exc()}")
            return None, time.monotonic() - started_at

        if not include_ignored:
            changes = [c for c in changes if c.change_type != ChangeType.IGNORED]

        if nested_worktree_paths:
            # git status reports a nested worktree checkout as a single untracked
            # directory entry (it never descends into another git repo), which
            # would otherwise surface as a spurious change for a folder that's
            # actually displayed separately as its own repo.
            repo_path_resolved = repo_path.resolve()
            worktree_paths_resolved = [p.resolve() for p in nested_worktree_paths]
            changes = [
                c
                for c in changes
                if not self._change_covers_worktree(
                    repo_path_resolved / c.path, worktree_paths_resolved
                )
            ]

        git_scan_ms = (time.monotonic() - started_at) * 1000
        pull_request = None
        if github_client is not None and not is_cancelled() and not skip_github_fetch:
            github_started_at = time.monotonic()
            pull_request = self._fetch_pull_request(
                adapter, branch_status.branch_name, github_client, on_log, previous_pull_request
            )
            github_fetch_ms = (time.monotonic() - github_started_at) * 1000
            on_log(
                f"{repo_path.name}: git scan {git_scan_ms:.0f}ms, github fetch {github_fetch_ms:.0f}ms"
            )
        elif skip_github_fetch:
            on_log(
                f"{repo_path.name}: skipping GitHub fetch (worktree not explicitly in profile)"
            )

        repo = Repository(
            path=repo_path,
            name=repo_path.name,
            branch_status=branch_status,
            changes=changes,
            pull_request=pull_request,
            logical_parent_path=logical_parent_path,
        )
        return repo, time.monotonic() - started_at

    @staticmethod
    def _repo_in_profile(
        repo_path: Path, worktree_parents: dict[Path, Path], profile_repo_names: set[str]
    ) -> bool:
        current: Path | None = repo_path
        while current is not None:
            if current.name in profile_repo_names:
                return True
            current = worktree_parents.get(current)
        return False

    @staticmethod
    def _change_covers_worktree(change_path: Path, worktree_paths: list[Path]) -> bool:
        return any(
            worktree_path == change_path or worktree_path.is_relative_to(change_path)
            for worktree_path in worktree_paths
        )

    @staticmethod
    def _fetch_pull_request(
        adapter: GitRepoAdapter,
        branch_name: str,
        github_client: GitHubClient,
        on_log: Callable[[str], None],
        previous_pull_request: tuple[PullRequestInfo, str] | None = None,
    ):
        if previous_pull_request is not None:
            prev_pr, prev_branch = previous_pull_request
            if prev_pr.state in ("merged", "closed") and branch_name == prev_branch:
                on_log(
                    f"Reusing cached PR for {prev_pr.repository} (terminal state {prev_pr.state})"
                )
                return prev_pr

        remote_url = adapter.get_remote_url("origin")
        if remote_url is None:
            return None
        owner_repo = parse_github_owner_repo(remote_url)
        if owner_repo is None:
            return None
        owner, repo_name = owner_repo
        try:
            return github_client.find_pull_request(owner, repo_name, branch_name)
        except GitHubError as exc:
            on_log(f"Failed to fetch GitHub PR status for {owner}/{repo_name}: {exc}")
            return None
