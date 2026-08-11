"""Persists the last scanned Workspace to disk so the GUI can paint the
folder tree instantly on the next launch instead of waiting on a cold git
scan (~10s across many repos). A missing, corrupt, or stale-schema cache is
a normal condition here, not an error: save/load never raise to the caller,
they just no-op / return None and let the app fall back to a fresh scan.
"""

import json
import time
from pathlib import Path

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.domain.workspace import Workspace

_CACHE_FILE_PATH = Path.home() / ".local-changes-viewer" / "workspace_cache.json"

# Bump this whenever the dict shapes below change. load_workspace() rejects
# a cache written by a different version instead of trying to migrate it or
# crashing on a field that no longer exists.
_CACHE_VERSION = 1

# Same directory, same JSON/version/try-except-swallow conventions as the
# workspace cache above, but a separate file: this one is small, keyed by
# remote URL, and has TTL/expiry semantics that don't fit the "wholesale
# overwrite on every scan" shape save_workspace() uses.
_DEFAULT_BRANCH_CACHE_FILE_PATH = (
    Path.home() / ".local-changes-viewer" / "default_branch_cache.json"
)
_DEFAULT_BRANCH_CACHE_VERSION = 1


def load_default_branch(remote_url: str, max_age_seconds: float) -> str | None:
    """Returns the cached default branch for `remote_url` if a fresh-enough
    entry exists, else None. GitRepoAdapter treats None as a cache miss and
    goes on to ask the remote directly."""
    try:
        data = json.loads(_DEFAULT_BRANCH_CACHE_FILE_PATH.read_text())
        if not isinstance(data, dict) or data.get("version") != _DEFAULT_BRANCH_CACHE_VERSION:
            return None
        entry = data.get("remotes", {}).get(remote_url)
        if not entry:
            return None
        if time.time() - entry["resolved_at"] > max_age_seconds:
            return None
        return entry["branch"]
    except Exception:
        return None


def save_default_branch(remote_url: str, branch_name: str) -> None:
    """Records `branch_name` as the resolved default branch for
    `remote_url`, timestamped now. Merges into the existing file (unlike
    save_workspace(), this cache accumulates one entry per remote rather
    than being replaced wholesale) so caching one repo's remote doesn't
    evict every other repo's already-cached entry."""
    try:
        try:
            data = json.loads(_DEFAULT_BRANCH_CACHE_FILE_PATH.read_text())
            if not isinstance(data, dict) or data.get("version") != _DEFAULT_BRANCH_CACHE_VERSION:
                data = {"version": _DEFAULT_BRANCH_CACHE_VERSION, "remotes": {}}
        except Exception:
            data = {"version": _DEFAULT_BRANCH_CACHE_VERSION, "remotes": {}}

        data.setdefault("remotes", {})[remote_url] = {
            "branch": branch_name,
            "resolved_at": time.time(),
        }
        _DEFAULT_BRANCH_CACHE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _DEFAULT_BRANCH_CACHE_FILE_PATH.write_text(json.dumps(data))
    except Exception:
        pass


def save_workspace(workspace: Workspace) -> None:
    try:
        data = {
            "version": _CACHE_VERSION,
            "root_path": str(workspace.root_path),
            "repositories": [_repository_to_dict(r) for r in workspace.repositories],
        }
        _CACHE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE_PATH.write_text(json.dumps(data))
    except Exception:
        pass


def load_workspace() -> Workspace | None:
    try:
        data = json.loads(_CACHE_FILE_PATH.read_text())
        if not isinstance(data, dict) or data.get("version") != _CACHE_VERSION:
            return None
        return Workspace(
            root_path=Path(data["root_path"]),
            repositories=[_repository_from_dict(r) for r in data["repositories"]],
        )
    except Exception:
        return None


def _repository_to_dict(repository: Repository) -> dict:
    return {
        "path": str(repository.path),
        "name": repository.name,
        "branch_status": _branch_status_to_dict(repository.branch_status),
        "changes": [_file_change_to_dict(c) for c in repository.changes],
        "pull_request": (
            _pull_request_to_dict(repository.pull_request)
            if repository.pull_request is not None
            else None
        ),
        "logical_parent_path": (
            str(repository.logical_parent_path)
            if repository.logical_parent_path is not None
            else None
        ),
    }


def _repository_from_dict(data: dict) -> Repository:
    pull_request = data.get("pull_request")
    logical_parent_path = data.get("logical_parent_path")
    return Repository(
        path=Path(data["path"]),
        name=data["name"],
        branch_status=_branch_status_from_dict(data["branch_status"]),
        changes=[_file_change_from_dict(c) for c in data["changes"]],
        pull_request=_pull_request_from_dict(pull_request) if pull_request is not None else None,
        logical_parent_path=(
            Path(logical_parent_path) if logical_parent_path is not None else None
        ),
    )


def _branch_status_to_dict(branch_status: BranchStatus) -> dict:
    return {
        "branch_name": branch_status.branch_name,
        "ahead": branch_status.ahead,
        "behind": branch_status.behind,
        "parent_branch": branch_status.parent_branch,
        "default_branch": branch_status.default_branch,
    }


def _branch_status_from_dict(data: dict) -> BranchStatus:
    return BranchStatus(
        branch_name=data["branch_name"],
        ahead=data["ahead"],
        behind=data["behind"],
        parent_branch=data.get("parent_branch"),
        default_branch=data.get("default_branch"),
    )


def _file_change_to_dict(change: FileChange) -> dict:
    return {
        "path": str(change.path),
        "change_type": change.change_type.value,
        "old_path": str(change.old_path) if change.old_path is not None else None,
        # Never persist the diff: it's a large hunk/line tree and the app
        # already recomputes it lazily on demand, so we drop it to null on
        # save and it always comes back as None on load.
        "diff": None,
        "is_directory": change.is_directory,
        "is_unpushed_commit": change.is_unpushed_commit,
        "commit_message": change.commit_message,
    }


def _file_change_from_dict(data: dict) -> FileChange:
    old_path = data.get("old_path")
    return FileChange(
        path=Path(data["path"]),
        change_type=ChangeType(data["change_type"]),
        old_path=Path(old_path) if old_path is not None else None,
        diff=None,
        is_directory=data.get("is_directory", False),
        is_unpushed_commit=data.get("is_unpushed_commit", False),
        commit_message=data.get("commit_message"),
    )


def _pull_request_to_dict(pull_request: PullRequestInfo) -> dict:
    return {
        "number": pull_request.number,
        "title": pull_request.title,
        "state": pull_request.state,
        "url": pull_request.url,
        "comment_count": pull_request.comment_count,
        "review_comment_count": pull_request.review_comment_count,
        "repository": pull_request.repository,
        "approved": pull_request.approved,
        "unresolved_review_thread_count": pull_request.unresolved_review_thread_count,
        "last_reviewer": pull_request.last_reviewer,
        "last_reviewed_at": pull_request.last_reviewed_at,
        "changed_files": pull_request.changed_files,
        "checks_state": pull_request.checks_state,
    }


def _pull_request_from_dict(data: dict) -> PullRequestInfo:
    return PullRequestInfo(
        number=data["number"],
        title=data["title"],
        state=data["state"],
        url=data["url"],
        comment_count=data["comment_count"],
        review_comment_count=data["review_comment_count"],
        repository=data.get("repository", ""),
        approved=data.get("approved"),
        unresolved_review_thread_count=data.get("unresolved_review_thread_count", 0),
        last_reviewer=data.get("last_reviewer"),
        last_reviewed_at=data.get("last_reviewed_at"),
        changed_files=data.get("changed_files", 0),
        checks_state=data.get("checks_state"),
    )
