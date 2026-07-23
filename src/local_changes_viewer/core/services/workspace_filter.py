from pathlib import Path

from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterRule
from local_changes_viewer.core.domain.repository import Repository
from local_changes_viewer.core.domain.workspace import Workspace


def _is_inside_filtered_folder(path: Path, rules: list[FolderFilterRule]) -> bool:
    for folder_name in path.parts[:-1]:
        for rule in rules:
            if rule.matches(folder_name):
                return True
    return False


def filter_workspace(
    workspace: Workspace,
    *,
    ignore_md_files: bool = False,
    hide_repos_without_changes: bool = False,
    folder_filter_rules: list[FolderFilterRule] | None = None,
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
