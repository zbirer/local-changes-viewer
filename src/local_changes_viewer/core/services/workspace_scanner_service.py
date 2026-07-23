import logging
from collections.abc import Callable
from pathlib import Path

from local_changes_viewer.core.domain.file_change import ChangeType
from local_changes_viewer.core.domain.repository import Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.infra.filesystem_scanner import FileSystemScanner
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter

logger = logging.getLogger(__name__)


class WorkspaceScannerService:
    def __init__(
        self,
        filesystem_scanner: FileSystemScanner | None = None,
        adapter_factory: Callable[[Path], GitRepoAdapter] | None = None,
    ) -> None:
        self._filesystem_scanner = filesystem_scanner or FileSystemScanner()
        self._adapter_factory = adapter_factory or GitRepoAdapter

    def scan(self, root: Path, include_ignored: bool = False) -> Workspace:
        repo_paths = self._filesystem_scanner.find_git_repos(root)
        repositories: list[Repository] = []

        for repo_path in repo_paths:
            try:
                adapter = self._adapter_factory(repo_path)
                changes = adapter.list_changes()
                branch_status = adapter.get_branch_status()
            except Exception:
                logger.warning("Skipping repo %s: failed to read git state", repo_path, exc_info=True)
                continue

            if not include_ignored:
                changes = [c for c in changes if c.change_type != ChangeType.IGNORED]

            repositories.append(
                Repository(
                    path=repo_path,
                    name=repo_path.name,
                    branch_status=branch_status,
                    changes=changes,
                )
            )

        return Workspace(root_path=root, repositories=repositories)
