import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from local_changes_viewer.core.domain.file_change import ChangeType
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
    ) -> Workspace:
        on_progress = on_progress or (lambda _message: None)
        on_repo_ready = on_repo_ready or (lambda _repo: None)
        on_log = on_log or (lambda _message: None)

        scan_started_at = time.monotonic()
        on_progress("Discovering git repositories…")
        discovery_started_at = time.monotonic()
        repo_paths = self._filesystem_scanner.find_git_repos(root)
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
                    repo_path, include_ignored, github_client, on_log
                ),
                repo_paths,
            )
            for index, (repo_path, timed_repo) in enumerate(zip(repo_paths, results), start=1):
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

    def _scan_repo(
        self,
        repo_path: Path,
        include_ignored: bool,
        github_client: GitHubClient | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> tuple[Repository | None, float]:
        on_log = on_log or (lambda _message: None)
        started_at = time.monotonic()
        try:
            adapter = self._adapter_factory(repo_path)
            changes = adapter.list_changes()
            branch_status = adapter.get_branch_status()
        except Exception as exc:
            on_log(f"Skipping repo {repo_path}: failed to read git state: {exc}\n{traceback.format_exc()}")
            return None, time.monotonic() - started_at

        if not include_ignored:
            changes = [c for c in changes if c.change_type != ChangeType.IGNORED]

        pull_request = None
        if github_client is not None:
            pull_request = self._fetch_pull_request(
                adapter, branch_status.branch_name, github_client, on_log
            )

        repo = Repository(
            path=repo_path,
            name=repo_path.name,
            branch_status=branch_status,
            changes=changes,
            pull_request=pull_request,
        )
        return repo, time.monotonic() - started_at

    @staticmethod
    def _fetch_pull_request(
        adapter: GitRepoAdapter,
        branch_name: str,
        github_client: GitHubClient,
        on_log: Callable[[str], None],
    ):
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
