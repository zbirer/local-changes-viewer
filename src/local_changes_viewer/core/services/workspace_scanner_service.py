import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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

# Caches the outcome of a GitHub PR lookup per repo (including a "no open PR"
# result) for this long, keyed by branch. MainWindow keeps a single
# WorkspaceScannerService alive across scans (see ScanWorker), so this cache
# lives on the instance rather than at module scope — a module-level cache
# would leak across service instances and break test isolation. Without this,
# a branch with no open PR gets re-queried on every single refresh (e.g. every
# couple of seconds while a busy file watcher keeps triggering auto-refresh
# scans), hammering the GitHub API. Keyed by repo_path with the branch name
# stored alongside the cached value; a branch change invalidates the entry
# immediately regardless of how fresh it is.
_PR_REFETCH_INTERVAL_SECONDS = 60.0

# A repo that never becomes "dirty" per the file watcher (e.g. an editor that
# writes a tracked file in place, which fires QFileSystemWatcher.fileChanged
# rather than directoryChanged — see WorkspaceFileWatcher) would otherwise
# reuse its cached git state forever on auto-refresh. This is the backstop:
# once a repo's last *real* scan is this old, an auto-refresh rescans it for
# real even though nothing marked it dirty.
_MAX_REPO_CACHE_AGE_SECONDS = 120.0

# Bounds how many age-triggered rescans a single auto-refresh tick can add on
# top of the dirty ones, so a workspace with many repos that all crossed the
# age floor at once can't balloon a fast incremental tick back into a full,
# slow cold-start-sized scan.
_MAX_AGE_TRIGGERED_RESCANS_PER_TICK = 4


@dataclass
class ScanVerificationResult:
    """One repo's outcome from `WorkspaceScannerService.verify_changes_against_git`."""

    repo_path: Path
    repo_name: str
    missing_from_app: list[Path] = field(default_factory=list)
    stale_in_app: list[Path] = field(default_factory=list)
    error: str | None = None

    @property
    def is_consistent(self) -> bool:
        return not self.missing_from_app and not self.stale_in_app and self.error is None


class WorkspaceScannerService:
    def __init__(
        self,
        filesystem_scanner: FileSystemScanner | None = None,
        adapter_factory: Callable[[Path], GitRepoAdapter] | None = None,
    ) -> None:
        self._filesystem_scanner = filesystem_scanner or FileSystemScanner()
        self._adapter_factory = adapter_factory or GitRepoAdapter
        self._repo_cache: dict[Path, Repository] = {}
        self._pr_fetch_cache: dict[Path, tuple[str, PullRequestInfo | None, float]] = {}
        # Wall-clock (monotonic) time of the last *real* (non-cache-reused) scan
        # per repo, independent of self._repo_cache's contents — this is what
        # the age floor above measures staleness against.
        self._last_real_scan_at: dict[Path, float] = {}

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
        dirty_paths: set[Path] | None = None,
        force_full_rescan: bool = False,
        on_debug: Callable[[str], None] | None = None,
    ) -> Workspace:
        on_progress = on_progress or (lambda _message: None)
        on_repo_ready = on_repo_ready or (lambda _repo: None)
        on_log = on_log or (lambda _message: None)
        on_debug = on_debug or (lambda _message: None)
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
        total_git_scan_seconds = 0.0
        total_github_fetch_seconds = 0.0

        # Repos the dirty-paths gate alone would skip forever if they never
        # fire directoryChanged again (an in-place edit to an already-tracked
        # file only fires fileChanged — see WorkspaceFileWatcher). This picks
        # up to _MAX_AGE_TRIGGERED_RESCANS_PER_TICK of the least-recently
        # *really* scanned repos whose cache has crossed the age floor, on top
        # of whatever dirty_paths already covers. Only meaningful for the
        # auto-refresh shape of call (dirty_paths is an actual set, not None,
        # and the caller isn't already forcing a full rescan of everything).
        age_triggered_paths: set[Path] = set()
        if dirty_paths is not None and not force_full_rescan:
            now = time.monotonic()
            stale_candidates: list[tuple[float, Path]] = []
            for repo_path in repo_paths:
                if repo_path in dirty_paths or repo_path not in self._repo_cache:
                    continue
                age_seconds = now - self._last_real_scan_at.get(repo_path, 0.0)
                if age_seconds >= _MAX_REPO_CACHE_AGE_SECONDS:
                    stale_candidates.append((age_seconds, repo_path))
            # Oldest (largest age) first, so the cap always picks the repos
            # that have gone longest without a real scan.
            stale_candidates.sort(key=lambda item: item[0], reverse=True)
            age_triggered_paths = {
                path for _, path in stale_candidates[:_MAX_AGE_TRIGGERED_RESCANS_PER_TICK]
            }
            if len(stale_candidates) > _MAX_AGE_TRIGGERED_RESCANS_PER_TICK:
                on_debug(
                    f"Age-based rescan cap reached: {len(stale_candidates)} repos past "
                    f"{_MAX_REPO_CACHE_AGE_SECONDS:.0f}s stale, rescanning only the "
                    f"{_MAX_AGE_TRIGGERED_RESCANS_PER_TICK} least-recently-scanned"
                )

        # Each repo scan is I/O-bound (shells out to git), so scanning repos in
        # parallel cuts wall-clock time roughly by the pool size. executor.map
        # yields results in submission order even though work completes out of
        # order, keeping progress messages and results deterministic.
        git_scan_phase_started_at = time.monotonic()
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
                    dirty_paths,
                    force_full_rescan,
                    age_triggered_paths,
                ),
                repo_paths,
            )
            rescanned_count = 0
            cached_count = 0
            for index, (repo_path, timed_repo) in enumerate(zip(repo_paths, results), start=1):
                if is_cancelled():
                    break
                repo, git_scan_seconds, github_fetch_seconds, trigger, cache_age_seconds = (
                    timed_repo
                )
                total_git_scan_seconds += git_scan_seconds
                total_github_fetch_seconds += github_fetch_seconds
                repo_seconds = git_scan_seconds + github_fetch_seconds
                on_progress(
                    f"Scanned {index}/{total}: {repo_path.name}… ({repo_seconds:.2f}s)"
                )
                if trigger == "cached":
                    cached_count += 1
                else:
                    rescanned_count += 1
                on_debug(
                    f"{repo_path.name}: scan decision={trigger} "
                    f"(cache age {cache_age_seconds:.0f}s)"
                )
                if repo is not None:
                    repositories.append(repo)
                    on_repo_ready(repo)
        on_debug(f"Scan decisions: {rescanned_count} rescanned, {cached_count} served from cache")
        git_scan_phase_seconds = time.monotonic() - git_scan_phase_started_at
        on_progress(
            f"Repo scan phase done in {git_scan_phase_seconds:.2f}s — git "
            f"{total_git_scan_seconds:.2f}s (aggregate), GitHub fetch "
            f"{total_github_fetch_seconds:.2f}s (aggregate)"
        )

        total_seconds = time.monotonic() - scan_started_at
        on_progress(
            f"Scan finished in {total_seconds:.2f}s — {len(repositories)}/{total} repos scanned"
        )

        self._repo_cache = {repo.path: repo for repo in repositories}

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

        # A single-repo refresh is always explicitly user-initiated (e.g. the
        # "Refresh Repo" context menu action), so it forces a real rescan the
        # same way the whole-workspace manual refresh does (Part 1) — never
        # reuse a cached git state here.
        repo, _, _, _, _ = self._scan_repo(
            repo_path,
            include_ignored,
            github_client,
            on_log,
            previous_pull_request,
            logical_parent_path,
            nested_worktree_paths,
            None,
            include_unpushed_commits,
            False,
            None,
            True,
            None,
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
        dirty_paths: set[Path] | None = None,
        force_full_rescan: bool = False,
        age_triggered_paths: set[Path] | None = None,
    ) -> tuple[Repository | None, float, float, str, float]:
        on_log = on_log or (lambda _message: None)
        is_cancelled = is_cancelled or (lambda: False)
        started_at = time.monotonic()
        if is_cancelled():
            return None, 0.0, 0.0, "cancelled", 0.0

        adapter = self._adapter_factory(repo_path)
        cached_repo = self._repo_cache.get(repo_path)
        last_real_scan_at = self._last_real_scan_at.get(repo_path)
        cache_age_seconds = (
            started_at - last_real_scan_at if last_real_scan_at is not None else 0.0
        )

        # Every branch below except "cached" performs a real git scan; the
        # label is what Part 2 of the fix and its logging hang on (and is
        # exactly what would have made the original bug — a repo stuck
        # replaying a stale cache forever — self-evident in the log).
        if cached_repo is None:
            trigger = "startup"
        elif force_full_rescan:
            trigger = "forced"
        elif dirty_paths is None:
            # No dirty-path tracking supplied at all for this call (e.g. a
            # settings toggle triggered the scan) — always do a real scan.
            trigger = "no-dirty-tracking"
        elif repo_path in dirty_paths:
            trigger = "dirty"
        elif age_triggered_paths is not None and repo_path in age_triggered_paths:
            trigger = "age"
        else:
            trigger = "cached"

        reuse_cached_git_state = trigger == "cached"

        if reuse_cached_git_state:
            changes = cached_repo.changes
            branch_status = cached_repo.branch_status
        else:
            try:
                changes = adapter.list_changes(include_unpushed_commits=include_unpushed_commits)
                branch_status = adapter.get_branch_status()
            except Exception as exc:
                on_log(f"Skipping repo {repo_path}: failed to read git state: {exc}\n{traceback.format_exc()}")
                return None, time.monotonic() - started_at, 0.0, trigger, cache_age_seconds

            self._last_real_scan_at[repo_path] = started_at

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

        git_scan_seconds = time.monotonic() - started_at
        pull_request = None
        github_fetch_seconds = 0.0
        if github_client is not None and not is_cancelled() and not skip_github_fetch:
            github_started_at = time.monotonic()
            pull_request = self._fetch_pull_request(
                repo_path,
                adapter,
                branch_status.branch_name,
                github_client,
                on_log,
                previous_pull_request,
            )
            github_fetch_seconds = time.monotonic() - github_started_at
            on_log(
                f"{repo_path.name}: git scan {git_scan_seconds * 1000:.0f}ms, "
                f"github fetch {github_fetch_seconds * 1000:.0f}ms"
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
        return repo, git_scan_seconds, github_fetch_seconds, trigger, cache_age_seconds

    def verify_changes_against_git(
        self,
        workspace: Workspace,
        include_ignored: bool = False,
        include_unpushed_commits: bool = False,
    ) -> list[ScanVerificationResult]:
        """Self-check: re-runs `git status` live for every repo in `workspace`
        and diffs it against `repo.changes`, in both directions. This is the
        validation command called for by the stale-cache bug this module
        exists to fix — a repo silently stuck replaying old changes shows up
        here as `missing_from_app` (git has it, the cached Repository
        doesn't), the mirror-image symptom of `stale_in_app` (the cache
        outlived a change that git no longer reports)."""
        results: list[ScanVerificationResult] = []
        for repo in workspace.repositories:
            try:
                adapter = self._adapter_factory(repo.path)
                live_changes = adapter.list_changes(
                    include_unpushed_commits=include_unpushed_commits
                )
            except Exception as exc:
                results.append(
                    ScanVerificationResult(
                        repo_path=repo.path, repo_name=repo.name, error=str(exc)
                    )
                )
                continue

            if not include_ignored:
                live_changes = [c for c in live_changes if c.change_type != ChangeType.IGNORED]

            live_paths = {c.path for c in live_changes}
            cached_paths = {c.path for c in repo.changes}
            results.append(
                ScanVerificationResult(
                    repo_path=repo.path,
                    repo_name=repo.name,
                    missing_from_app=sorted(live_paths - cached_paths),
                    stale_in_app=sorted(cached_paths - live_paths),
                )
            )
        return results

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

    def _fetch_pull_request(
        self,
        repo_path: Path,
        adapter: GitRepoAdapter,
        branch_name: str,
        github_client: GitHubClient,
        on_log: Callable[[str], None],
        previous_pull_request: tuple[PullRequestInfo | None, str] | None = None,
    ):
        # TTL cache first: applies whether or not there's an open PR, so a
        # branch with no PR at all stops being re-queried on every refresh
        # (previously only a *found* PR was ever reused — see module docstring
        # above _PR_REFETCH_INTERVAL_SECONDS). A branch change bypasses the
        # cache outright.
        cached = self._pr_fetch_cache.get(repo_path)
        if cached is not None:
            cached_branch, cached_pr, fetched_at = cached
            if (
                cached_branch == branch_name
                and (time.monotonic() - fetched_at) < _PR_REFETCH_INTERVAL_SECONDS
            ):
                return cached_pr

        if previous_pull_request is not None:
            prev_pr, prev_branch = previous_pull_request
            if (
                prev_pr is not None
                and prev_pr.state in ("merged", "closed")
                and branch_name == prev_branch
            ):
                on_log(
                    f"Reusing cached PR for {prev_pr.repository} (terminal state {prev_pr.state})"
                )
                self._pr_fetch_cache[repo_path] = (branch_name, prev_pr, time.monotonic())
                return prev_pr

        remote_url = adapter.get_remote_url("origin")
        if remote_url is None:
            self._pr_fetch_cache[repo_path] = (branch_name, None, time.monotonic())
            return None
        owner_repo = parse_github_owner_repo(remote_url)
        if owner_repo is None:
            self._pr_fetch_cache[repo_path] = (branch_name, None, time.monotonic())
            return None
        owner, repo_name = owner_repo
        try:
            pull_request = github_client.find_pull_request(owner, repo_name, branch_name)
        except GitHubError as exc:
            on_log(f"Failed to fetch GitHub PR status for {owner}/{repo_name}: {exc}")
            pull_request = None
        self._pr_fetch_cache[repo_path] = (branch_name, pull_request, time.monotonic())
        return pull_request
