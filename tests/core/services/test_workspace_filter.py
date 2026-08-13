import os
import time
from pathlib import Path

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterMode, FolderFilterRule
from local_changes_viewer.core.domain.profile import Profile
from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.services.workspace_filter import filter_workspace

_BRANCH = BranchStatus(branch_name="main", ahead=0, behind=0)


def _repo(name: str, changes: list[FileChange]) -> Repository:
    return Repository(path=Path(f"/repos/{name}"), name=name, branch_status=_BRANCH, changes=changes)


def test_no_filters_returns_equivalent_workspace() -> None:
    changes = [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])

    result = filter_workspace(workspace)

    assert len(result.repositories) == 1
    assert result.repositories[0].changes == changes


def test_ignore_md_files_filters_by_suffix_case_insensitive() -> None:
    changes = [
        FileChange(path=Path("README.md"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("NOTES.MD"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED),
    ]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])

    result = filter_workspace(workspace, ignore_md_files=True)

    assert [c.path for c in result.repositories[0].changes] == [Path("a.py")]


def test_hide_repos_without_changes_drops_empty_repos() -> None:
    repo_with_changes = _repo(
        "repo_a", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]
    )
    repo_without_changes = _repo("repo_b", [])
    workspace = Workspace(
        root_path=Path("/root"), repositories=[repo_with_changes, repo_without_changes]
    )

    result = filter_workspace(workspace, hide_repos_without_changes=True)

    assert [r.name for r in result.repositories] == ["repo_a"]


def test_hide_repos_without_changes_after_ignoring_md_files() -> None:
    md_only_repo = _repo(
        "repo_md_only", [FileChange(path=Path("README.md"), change_type=ChangeType.MODIFIED)]
    )
    mixed_repo = _repo(
        "repo_mixed",
        [
            FileChange(path=Path("README.md"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED),
        ],
    )
    workspace = Workspace(root_path=Path("/root"), repositories=[md_only_repo, mixed_repo])

    result = filter_workspace(workspace, ignore_md_files=True, hide_repos_without_changes=True)

    assert [r.name for r in result.repositories] == ["repo_mixed"]


def test_folder_filter_rule_equals_removes_symlinked_directory_entry() -> None:
    # A symlinked node_modules is reported by git as a top-level, no-slash
    # path, but classified is_directory=True by GitRepoAdapter (Part 4 of the
    # stale-cache fix) so it goes through the is_directory branch here
    # (change.path.parts, not parts[:-1]) and still matches the rule.
    changes = [
        FileChange(
            path=Path("node_modules"),
            change_type=ChangeType.UNTRACKED,
            is_directory=True,
        ),
        FileChange(path=Path("c.py"), change_type=ChangeType.MODIFIED),
    ]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])
    rules = [FolderFilterRule(text="node_modules", mode=FolderFilterMode.EQUALS)]

    result = filter_workspace(workspace, folder_filter_rules=rules)

    assert [c.path for c in result.repositories[0].changes] == [Path("c.py")]


def test_folder_filter_rule_equals_matches_full_folder_name_only() -> None:
    changes = [
        FileChange(path=Path("build/a.py"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("build_tools/b.py"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("c.py"), change_type=ChangeType.MODIFIED),
    ]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])
    rules = [FolderFilterRule(text="build", mode=FolderFilterMode.EQUALS)]

    result = filter_workspace(workspace, folder_filter_rules=rules)

    assert [c.path for c in result.repositories[0].changes] == [
        Path("build_tools/b.py"),
        Path("c.py"),
    ]


def test_folder_filter_rule_contains_matches_substring() -> None:
    changes = [
        FileChange(path=Path("node_modules/pkg/a.py"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("src/b.py"), change_type=ChangeType.MODIFIED),
    ]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])
    rules = [FolderFilterRule(text="node_", mode=FolderFilterMode.CONTAINS)]

    result = filter_workspace(workspace, folder_filter_rules=rules)

    assert [c.path for c in result.repositories[0].changes] == [Path("src/b.py")]


def test_folder_filter_rule_checks_any_ancestor_folder_not_filename() -> None:
    changes = [
        FileChange(path=Path("vendor/deep/file.py"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("vendor.py"), change_type=ChangeType.MODIFIED),
    ]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])
    rules = [FolderFilterRule(text="vendor", mode=FolderFilterMode.EQUALS)]

    result = filter_workspace(workspace, folder_filter_rules=rules)

    assert [c.path for c in result.repositories[0].changes] == [Path("vendor.py")]


def test_folder_filter_rule_file_path_matches_only_that_exact_file() -> None:
    changes = [
        FileChange(path=Path("src/analytics/events/clicked-log-call.ts"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("src/analytics/events/other-file.ts"), change_type=ChangeType.MODIFIED),
    ]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])
    rules = [
        FolderFilterRule(
            text="src/analytics/events/clicked-log-call.ts", mode=FolderFilterMode.FILE_PATH
        )
    ]

    result = filter_workspace(workspace, folder_filter_rules=rules)

    assert [c.path for c in result.repositories[0].changes] == [
        Path("src/analytics/events/other-file.ts")
    ]


def test_folder_filter_rule_matches_untracked_directory_leaf_entry() -> None:
    changes = [
        FileChange(
            path=Path("node_modules"),
            change_type=ChangeType.UNTRACKED,
            is_directory=True,
        ),
        FileChange(path=Path("src/b.py"), change_type=ChangeType.MODIFIED),
    ]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])
    rules = [FolderFilterRule(text="node_modules", mode=FolderFilterMode.EQUALS)]

    result = filter_workspace(workspace, folder_filter_rules=rules)

    assert [c.path for c in result.repositories[0].changes] == [Path("src/b.py")]


def test_folder_filter_rule_excludes_nested_repo_under_filtered_folder() -> None:
    root = Path("/root")
    kept_repo = Repository(
        path=root / "server",
        name="server",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)],
    )
    nested_repo_in_node_modules = Repository(
        path=root / "server" / "node_modules" / "some-pkg",
        name="some-pkg",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("b.py"), change_type=ChangeType.MODIFIED)],
    )
    nested_repo_in_claude = Repository(
        path=root / "server" / ".claude" / "worktrees" / "wt",
        name="wt",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("c.py"), change_type=ChangeType.MODIFIED)],
    )
    workspace = Workspace(
        root_path=root,
        repositories=[kept_repo, nested_repo_in_node_modules, nested_repo_in_claude],
    )
    rules = [
        FolderFilterRule(text="node_modules", mode=FolderFilterMode.EQUALS),
        FolderFilterRule(text=".claude", mode=FolderFilterMode.CONTAINS),
    ]

    result = filter_workspace(workspace, folder_filter_rules=rules)

    assert [r.name for r in result.repositories] == ["server"]


def test_preserves_logical_parent_path_through_filtering() -> None:
    root = Path("/root")
    parent_repo = Repository(
        path=root / "server",
        name="server",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)],
    )
    worktree_repo = Repository(
        path=root / "server-worktrees" / "pr-1",
        name="pr-1",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("b.py"), change_type=ChangeType.MODIFIED)],
        logical_parent_path=root / "server",
    )
    workspace = Workspace(root_path=root, repositories=[parent_repo, worktree_repo])

    result = filter_workspace(workspace)

    worktree_result = next(r for r in result.repositories if r.name == "pr-1")
    assert worktree_result.logical_parent_path == root / "server"


def test_profile_keeps_nested_worktree_child_of_matching_parent() -> None:
    root = Path("/root")
    parent_repo = Repository(
        path=root / "dashboard",
        name="dashboard",
        branch_status=_BRANCH,
        changes=[],
    )
    worktree_repo = Repository(
        path=root / "dashboard" / ".worktrees" / "pr-4161",
        name="pr-4161",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)],
        logical_parent_path=root / "dashboard",
    )
    other_repo = Repository(
        path=root / "unrelated",
        name="unrelated",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("b.py"), change_type=ChangeType.MODIFIED)],
    )
    workspace = Workspace(
        root_path=root, repositories=[parent_repo, worktree_repo, other_repo]
    )
    profile = Profile(name="main", repo_names=["dashboard"])

    result = filter_workspace(
        workspace, profile=profile, hide_repos_without_changes=True
    )

    assert [r.name for r in result.repositories] == ["dashboard", "pr-4161"]


def test_does_not_mutate_original_repository_changes() -> None:
    changes = [
        FileChange(path=Path("README.md"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED),
    ]
    original_repo = _repo("repo_a", changes)
    workspace = Workspace(root_path=Path("/root"), repositories=[original_repo])

    filter_workspace(workspace, ignore_md_files=True)

    assert len(original_repo.changes) == 2


def test_max_age_minutes_zero_shows_all_changes() -> None:
    changes = [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])

    result = filter_workspace(workspace, max_age_minutes=0)

    assert result.repositories[0].changes == changes


def test_max_age_minutes_filters_out_old_files(tmp_path: Path) -> None:
    recent_file = tmp_path / "recent.py"
    recent_file.write_text("recent")
    old_file = tmp_path / "old.py"
    old_file.write_text("old")
    old_mtime = time.time() - 3600
    os.utime(old_file, (old_mtime, old_mtime))

    changes = [
        FileChange(path=Path("recent.py"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("old.py"), change_type=ChangeType.MODIFIED),
    ]
    repo = Repository(path=tmp_path, name="repo_a", branch_status=_BRANCH, changes=changes)
    workspace = Workspace(root_path=tmp_path, repositories=[repo])

    result = filter_workspace(workspace, max_age_minutes=5)

    assert [c.path for c in result.repositories[0].changes] == [Path("recent.py")]


def test_pull_request_survives_filtering() -> None:
    """The rebuild inside filter_workspace used to construct a fresh
    Repository listing only some fields, silently dropping pull_request
    (which defaults to None) even though nothing here is supposed to touch
    it. An active folder filter is enough to exercise that rebuild path."""
    pull_request = PullRequestInfo(
        number=42,
        title="Add feature",
        state="open",
        url="https://example.com/pr/42",
        comment_count=1,
        review_comment_count=0,
    )
    repo = Repository(
        path=Path("/repos/repo_a"),
        name="repo_a",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)],
        pull_request=pull_request,
    )
    workspace = Workspace(root_path=Path("/root"), repositories=[repo])
    rules = [FolderFilterRule(text="build", mode=FolderFilterMode.EQUALS)]

    result = filter_workspace(workspace, folder_filter_rules=rules)

    assert result.repositories[0].pull_request == pull_request


def test_max_age_minutes_includes_change_when_file_missing(tmp_path: Path) -> None:
    changes = [FileChange(path=Path("deleted.py"), change_type=ChangeType.DELETED)]
    repo = Repository(path=tmp_path, name="repo_a", branch_status=_BRANCH, changes=changes)
    workspace = Workspace(root_path=tmp_path, repositories=[repo])

    result = filter_workspace(workspace, max_age_minutes=5)

    assert [c.path for c in result.repositories[0].changes] == [Path("deleted.py")]


def test_no_filters_configured_emits_no_log_messages() -> None:
    changes = [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])
    messages: list[str] = []

    filter_workspace(workspace, on_log=messages.append)

    assert messages == []


def test_filter_workspace_works_without_on_log() -> None:
    changes = [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]
    workspace = Workspace(root_path=Path("/root"), repositories=[_repo("repo_a", changes)])

    result = filter_workspace(workspace, ignore_md_files=True, hide_repos_without_changes=True)

    assert [r.name for r in result.repositories] == ["repo_a"]


def test_logs_repo_dropped_by_profile_filter() -> None:
    repo = _repo("repo_a", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)])
    workspace = Workspace(root_path=Path("/root"), repositories=[repo])
    profile = Profile(name="other", repo_names=["repo_b"])
    messages: list[str] = []

    result = filter_workspace(workspace, profile=profile, on_log=messages.append)

    assert result.repositories == []
    assert any(
        str(repo.path) in msg and "other" in msg and "profile" in msg for msg in messages
    )


def test_logs_repo_dropped_by_folder_filter_rule_naming_rule_and_segment() -> None:
    root = Path("/root/CanopyOS")
    repo = Repository(
        path=root / ".claude" / "worktrees" / "bug-eh-11913-114985",
        name="bug-eh-11913-114985",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("features/product-spec.md"), change_type=ChangeType.MODIFIED)],
    )
    workspace = Workspace(root_path=root, repositories=[repo])
    rules = [FolderFilterRule(text=".claude", mode=FolderFilterMode.CONTAINS)]
    messages: list[str] = []

    result = filter_workspace(workspace, folder_filter_rules=rules, on_log=messages.append)

    assert result.repositories == []
    assert len(messages) == 1
    message = messages[0]
    assert str(repo.path) in message
    assert "contains:'.claude'" in message
    assert "'.claude'" in message


def test_logs_changes_stripped_by_ignore_md_files() -> None:
    changes = [
        FileChange(path=Path("README.md"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED),
    ]
    repo = _repo("repo_a", changes)
    workspace = Workspace(root_path=Path("/root"), repositories=[repo])
    messages: list[str] = []

    filter_workspace(workspace, ignore_md_files=True, on_log=messages.append)

    assert any(str(repo.path) in msg and "1" in msg for msg in messages)


def test_logs_changes_stripped_by_folder_filter_rules() -> None:
    changes = [
        FileChange(path=Path("node_modules/pkg/a.py"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("src/b.py"), change_type=ChangeType.MODIFIED),
    ]
    repo = _repo("repo_a", changes)
    workspace = Workspace(root_path=Path("/root"), repositories=[repo])
    rules = [FolderFilterRule(text="node_modules", mode=FolderFilterMode.EQUALS)]
    messages: list[str] = []

    filter_workspace(workspace, folder_filter_rules=rules, on_log=messages.append)

    assert any(str(repo.path) in msg and "folder filter" in msg for msg in messages)


def test_logs_changes_stripped_by_max_age_minutes(tmp_path: Path) -> None:
    old_file = tmp_path / "old.py"
    old_file.write_text("old")
    old_mtime = time.time() - 3600
    os.utime(old_file, (old_mtime, old_mtime))
    changes = [FileChange(path=Path("old.py"), change_type=ChangeType.MODIFIED)]
    repo = Repository(path=tmp_path, name="repo_a", branch_status=_BRANCH, changes=changes)
    workspace = Workspace(root_path=tmp_path, repositories=[repo])
    messages: list[str] = []

    filter_workspace(workspace, max_age_minutes=5, on_log=messages.append)

    assert any(str(repo.path) in msg and "max_age_minutes" in msg for msg in messages)


def test_logs_repo_hidden_because_no_changes() -> None:
    repo = _repo("repo_a", [])
    workspace = Workspace(root_path=Path("/root"), repositories=[repo])
    messages: list[str] = []

    filter_workspace(workspace, hide_repos_without_changes=True, on_log=messages.append)

    assert any(str(repo.path) in msg and "no changes" in msg for msg in messages)


def test_hide_repos_without_changes_never_hides_worktree_with_no_changes() -> None:
    """Worktrees are navigational structure the user relies on to jump
    between branches (mirroring what "List Worktrees" already shows
    unconditionally), so they must always render — clean or dirty — even
    when the "hide repos without changes" setting is on."""
    root = Path("/root")
    parent_repo = Repository(
        path=root / "dashboard",
        name="dashboard",
        branch_status=_BRANCH,
        changes=[FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)],
    )
    clean_worktree = Repository(
        path=root / "dashboard" / ".worktrees" / "clean-wt",
        name="clean-wt",
        branch_status=_BRANCH,
        changes=[],
        logical_parent_path=root / "dashboard",
    )
    workspace = Workspace(root_path=root, repositories=[parent_repo, clean_worktree])

    result = filter_workspace(workspace, hide_repos_without_changes=True)

    assert [r.name for r in result.repositories] == ["dashboard", "clean-wt"]


def test_hide_repos_without_changes_still_drops_empty_top_level_repo() -> None:
    """Regression guard for the worktree exemption above: a regular
    top-level repo (no logical_parent_path) with zero changes must still
    be dropped exactly as before."""
    repo_with_changes = _repo(
        "repo_a", [FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED)]
    )
    empty_top_level_repo = _repo("repo_b", [])
    workspace = Workspace(
        root_path=Path("/root"), repositories=[repo_with_changes, empty_top_level_repo]
    )

    result = filter_workspace(workspace, hide_repos_without_changes=True)

    assert [r.name for r in result.repositories] == ["repo_a"]


def test_no_hidden_log_for_worktree_with_no_changes() -> None:
    """The "hidden — no changes" log line must not fire for a worktree,
    since the new exemption means it isn't actually hidden."""
    root = Path("/root")
    clean_worktree = Repository(
        path=root / "dashboard" / ".worktrees" / "clean-wt",
        name="clean-wt",
        branch_status=_BRANCH,
        changes=[],
        logical_parent_path=root / "dashboard",
    )
    workspace = Workspace(root_path=root, repositories=[clean_worktree])
    messages: list[str] = []

    filter_workspace(workspace, hide_repos_without_changes=True, on_log=messages.append)

    assert messages == []
