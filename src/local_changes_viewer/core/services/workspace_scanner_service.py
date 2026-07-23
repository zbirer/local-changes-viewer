import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from local_changes_viewer.core.domain.file_change import ChangeType
from local_changes_viewer.core.domain.repository import Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.infra.filesystem_scanner import FileSystemScanner
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter

logger = logging.getLogger(__name__)

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
    ) -> Workspace:
        on_progress = on_progress or (lambda _message: None)
        on_repo_ready = on_repo_ready or (lambda _repo: None)

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
                lambda repo_path: self._scan_repo(repo_path, include_ignored), repo_paths
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
        self, repo_path: Path, include_ignored: bool
    ) -> tuple[Repository | None, float]:
        started_at = time.monotonic()
        try:
            adapter = self._adapter_factory(repo_path)
            changes = adapter.list_changes()
            branch_status = adapter.get_branch_status()
        except Exception:
            logger.warning("Skipping repo %s: failed to read git state", repo_path, exc_info=True)
            return None, time.monotonic() - started_at

        if not include_ignored:
            changes = [c for c in changes if c.change_type != ChangeType.IGNORED]

        repo = Repository(
            path=repo_path,
            name=repo_path.name,
            branch_status=branch_status,
            changes=changes,
        )
        return repo, time.monotonic() - started_at
