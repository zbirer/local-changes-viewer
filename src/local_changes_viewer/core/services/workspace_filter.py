import time
from collections.abc import Callable
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
) -> tuple[FolderFilterRule, str] | None:
    """Returns the (rule, path segment) that matched, or None if no rule matches.

    Returning the match (rather than a bare bool) lets callers log exactly which
    rule dropped the repo — critical because a rule intended for build output
    (e.g. `contains:'.claude'`) can also match a path segment of an unrelated
    repo/worktree location, silently dropping it with no other indication.
    """
    try:
        rel_parts = repo.path.relative_to(root_path).parts
    except ValueError:
        rel_parts = repo.path.parts
    for folder_name in rel_parts:
        for rule in rules:
            if rule.matches(folder_name):
                return rule, folder_name
    return None


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
    hide_changeless_worktrees: bool = False,
    folder_filter_rules: list[FolderFilterRule] | None = None,
    max_age_minutes: int = 0,
    profile: Profile | None = None,
    on_log: Callable[[str], None] | None = None,
) -> Workspace:
    on_log = on_log or (lambda _message: None)
    folder_filter_rules = folder_filter_rules or []
    all_by_path = {str(r.path): r for r in workspace.repositories}
    considered: list[Repository] = []
    for repo in workspace.repositories:
        if profile is not None and not _repo_matches_profile(repo, profile, all_by_path):
            on_log(f"{repo.path}: dropped — not in active profile {profile.name!r}")
            continue

        if folder_filter_rules:
            match = _repo_is_inside_filtered_folder(repo, workspace.root_path, folder_filter_rules)
            if match is not None:
                rule, segment = match
                on_log(
                    f"{repo.path}: dropped — inside filtered folder "
                    f"(rule {rule.mode.value}:{rule.text!r} matched segment {segment!r})"
                )
                continue

        changes = repo.changes

        if ignore_md_files:
            filtered = [c for c in changes if c.path.suffix.lower() != ".md"]
            dropped = len(changes) - len(filtered)
            if dropped:
                on_log(f"{repo.path}: ignore_md_files dropped {dropped} change(s)")
            changes = filtered

        if folder_filter_rules:
            filtered = [
                c for c in changes if not _is_inside_filtered_folder(c, folder_filter_rules)
            ]
            dropped = len(changes) - len(filtered)
            if dropped:
                on_log(f"{repo.path}: folder filter dropped {dropped} change(s)")
            changes = filtered

        if max_age_minutes > 0:
            filtered = [c for c in changes if _changed_within(repo.path, c, max_age_minutes)]
            dropped = len(changes) - len(filtered)
            if dropped:
                on_log(f"{repo.path}: max_age_minutes dropped {dropped} change(s)")
            changes = filtered

        # dataclasses.replace() carries every other field (notably
        # pull_request) through unchanged, so a future field added to
        # Repository can't be silently dropped here the way pull_request was.
        considered.append(replace(repo, changes=changes))

    if not hide_repos_without_changes and not hide_changeless_worktrees:
        return Workspace(root_path=workspace.root_path, repositories=considered)

    by_path = {str(r.path): r for r in considered}
    children_by_parent: dict[str, list[Repository]] = {}
    for r in considered:
        parent = r.logical_parent_path
        if parent is not None and str(parent) in by_path:
            children_by_parent.setdefault(str(parent), []).append(r)

    # Two independent, off-by-default switches share this loop, deliberately
    # kept as two separate booleans rather than one combined flag: worktrees
    # are navigational structure the user relies on to jump between branches
    # (mirroring what "List Worktrees" already shows unconditionally), so
    # "Hide repos without changes" (hide_repos_without_changes) exempts them
    # entirely -- see F35. But that exemption means a workspace with many
    # long-lived, mostly-clean worktrees has no way to declutter *them*
    # specifically, hence "Hide empty worktrees" (hide_changeless_worktrees,
    # F95) as its own opt-in control, so a user can hide clean worktrees
    # without also hiding clean regular repos, or vice versa. A parent repo
    # with no changes of its own is still kept when a nested worktree
    # beneath it has changes, so that worktree isn't orphaned as a
    # top-level item once its parent's row is dropped.
    repositories = []
    for r in considered:
        if r.logical_parent_path is not None:
            if hide_changeless_worktrees and not _repo_or_descendant_has_changes(
                r, children_by_parent
            ):
                on_log(f"{r.path}: hidden — worktree has no changes")
                continue
            repositories.append(r)
        elif hide_repos_without_changes:
            if _repo_or_descendant_has_changes(r, children_by_parent):
                repositories.append(r)
            else:
                on_log(f"{r.path}: hidden — no changes")
        else:
            repositories.append(r)
    return Workspace(root_path=workspace.root_path, repositories=repositories)
