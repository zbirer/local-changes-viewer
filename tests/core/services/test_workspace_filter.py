from pathlib import Path

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterMode, FolderFilterRule
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


def test_does_not_mutate_original_repository_changes() -> None:
    changes = [
        FileChange(path=Path("README.md"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("a.py"), change_type=ChangeType.MODIFIED),
    ]
    original_repo = _repo("repo_a", changes)
    workspace = Workspace(root_path=Path("/root"), repositories=[original_repo])

    filter_workspace(workspace, ignore_md_files=True)

    assert len(original_repo.changes) == 2
