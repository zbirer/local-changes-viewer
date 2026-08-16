"""Builds the "Create Patch" feature's git-apply-able patch text.

Thin wrapper over `GitRepoAdapter.build_patch`, mirroring `diff_service.py`'s
adapter-factory shape. The one thing this layer owns that the adapter can't:
deciding *which* untracked files belong to the requested target, since that
needs `Repository.changes` (the workspace's already-scanned state) rather
than a fresh disk/`git status` scan the adapter has no reason to repeat.
"""

from collections.abc import Callable
from pathlib import Path

from local_changes_viewer.core.domain.file_change import ChangeType
from local_changes_viewer.core.domain.repository import Repository
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter


class PatchService:
    def __init__(
        self,
        adapter_factory: Callable[[Path], GitRepoAdapter] | None = None,
    ) -> None:
        self._adapter_factory = adapter_factory or GitRepoAdapter

    def build_patch(self, repo: Repository, target_relpath: Path) -> str:
        """Builds a patch for `target_relpath` (repo-relative; `Path(".")` means
        the whole repo) covering every change under it, tracked or untracked.

        `Path.is_relative_to` also returns True when the two paths are equal, so
        this one check covers a single file selected directly, a subfolder, and
        the repo-root case (`Path(".").is_relative_to(...)` matches everything)
        without special-casing any of them.
        """
        untracked_paths = [
            change.path
            for change in repo.changes
            if change.change_type == ChangeType.UNTRACKED
            and change.path.is_relative_to(target_relpath)
        ]
        adapter = self._adapter_factory(repo.path)
        return adapter.build_patch(target_relpath, untracked_paths)
