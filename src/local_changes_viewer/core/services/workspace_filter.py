import time
from dataclasses import replace
from pathlib import Path

from local_changes_viewer.core.domain.file_change import FileChange
from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterRule
from local_changes_viewer.core.domain.profile import Profile
from local_changes_viewer.core.domain.repository import Repository
from local_changes_viewer.core.domain.workspace import Workspace


def _is_inside_filtered_folder(change: FileChange, rules: list[FolderFilterRule]) -> bool:
    if not change.is_directory and any(rule.matches_path(change.path.as_posix()) for rule in rules):
        return True
    parts = change.path.parts if change.is_directory else change.path.parts[:-1]
    for folder_name in parts:
        for rule in rules:
            if rule.matches(folder_name):
                return True
    return False


def _repo_is_inside_filtered_folder(
    repo: Repository, root_path: Path, rules: list[FolderFilterRule]
) -> bool:
    try:
        rel_parts = repo.path.relative_to(root_path).parts
    except ValueError:
        rel_parts = repo.path.parts
    for folder_name in rel_parts:
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


def _repo_matches_profile(
    repo: Repository, profile: Profile, by_path: dict[str, Repository]
) -> bool:
    current: Repository | None = repo
    while current is not None:
        if current.name in profile.repo_names:
            return True
        parent = current.logical_parent_path
        current = by_path.get(str(parent)) if parent is not None else None
    return False


def _repo_or_descendant_has_changes(
    repo: Repository, children_by_parent: dict[str, list[Repository]]
) -> bool:
    if repo.changes:
        return True
    return any(
        _repo_or_descendant_has_changes(child, children_by_parent)
        for child in children_by_parent.get(str(repo.path), [])
    )


def filter_workspace(
    workspace: Workspace,
    *,
    ignore_md_files: bool = False,
    hide_repos_without_changes: bool = False,
    folder_filter_rules: list[FolderFilterRule] | None = None,
    max_age_minutes: int = 0,
    profile: Profile | None = None,
) -> Workspace:
    folder_filter_rules = folder_filter_rules or []
    all_by_path = {str(r.path): r for r in workspace.repositories}
    considered: list[Repository] = []
    for repo in workspace.repositories:
        if profile is not None and not _repo_matches_profile(repo, profile, all_by_path):
            continue

        if folder_filter_rules and _repo_is_inside_filtered_folder(
            repo, workspace.root_path, folder_filter_rules
        ):
            continue

        changes = repo.changes
        if ignore_md_files:
            changes = [c for c in changes if c.path.suffix.lower() != ".md"]

        if folder_filter_rules:
            changes = [
                c for c in changes if not _is_inside_filtered_folder(c, folder_filter_rules)
            ]

        if max_age_minutes > 0:
            changes = [c for c in changes if _changed_within(repo.path, c, max_age_minutes)]

        # dataclasses.replace() carries every other field (notably
        # pull_request) through unchanged, so a future field added to
        # Repository can't be silently dropped here the way pull_request was.
        considered.append(replace(repo, changes=changes))

    if not hide_repos_without_changes:
        return Workspace(root_path=workspace.root_path, repositories=considered)

    by_path = {str(r.path): r for r in considered}
    children_by_parent: dict[str, list[Repository]] = {}
    for r in considered:
        parent = r.logical_parent_path
        if parent is not None and str(parent) in by_path:
            children_by_parent.setdefault(str(parent), []).append(r)

    # A parent repo with no changes of its own is still kept when a nested
    # worktree underneath it has changes, so that worktree isn't orphaned as
    # a top-level item once its parent's row is dropped.
    repositories = [
        r for r in considered if _repo_or_descendant_has_changes(r, children_by_parent)
    ]
    return Workspace(root_path=workspace.root_path, repositories=repositories)
