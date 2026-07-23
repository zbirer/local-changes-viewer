from collections.abc import Callable
from pathlib import Path

from local_changes_viewer.core.domain.diff import DiffResult
from local_changes_viewer.core.domain.file_change import FileChange
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter


class DiffService:
    def __init__(
        self,
        adapter_factory: Callable[[Path], GitRepoAdapter] | None = None,
    ) -> None:
        self._adapter_factory = adapter_factory or GitRepoAdapter

    def load_diff(
        self, repo_path: Path, change: FileChange, ignore_whitespace: bool = False
    ) -> DiffResult:
        adapter = self._adapter_factory(repo_path)
        return adapter.compute_diff(change, ignore_whitespace=ignore_whitespace)
