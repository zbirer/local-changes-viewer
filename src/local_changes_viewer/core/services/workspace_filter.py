import time
from pathlib import Path

from local_changes_viewer.core.domain.file_change import FileChange
from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterRule
from local_changes_viewer.core.domain.repository import Repository
from local_changes_viewer.core.domain.workspace import Workspace


def _is_inside_filtered_folder(path: Path, rules: list[FolderFilterRule]) -> bool:
    for folder_name in path.parts[:-1]:
        for rule in rules:
            if rule.matches(folder_name):
                return True
    return False


def _changed_within(repo_path: Path, change: FileChange, max_age_minutes: int) -> bool:
    try:
        mtime = (repo_path / change.path).stat().st_mtime
    except OSError:
        return True
    age_minutes = (time.time() - mtime) / 60
    return age_minutes <= max_age_minutes


def filter_workspace(
    workspace: Workspace,
    *,
    ignore_md_files: bool = False,
    hide_repos_without_changes: bool = False,
    folder_filter_rules: list[FolderFilterRule] | None = None,
    max_age_minutes: int = 0,
) -> Workspace:
    folder_filter_rules = folder_filter_rules or []
    repositories: list[Repository] = []
    for repo in workspace.repositories:
        changes = repo.changes
        if ignore_md_files:
            changes = [c for c in changes if c.path.suffix.lower() != ".md"]

        if folder_filter_rules:
            changes = [
                c for c in changes if not _is_inside_filtered_folder(c.path, folder_filter_rules)
            ]

        if max_age_minutes > 0:
            changes = [c for c in changes if _changed_within(repo.path, c, max_age_minutes)]

        if hide_repos_without_changes and not changes:
            continue

        repositories.append(
            Repository(
                path=repo.path,
                name=repo.name,
                branch_status=repo.branch_status,
                changes=changes,
            )
        )
    return Workspace(root_path=workspace.root_path, repositories=repositories)
