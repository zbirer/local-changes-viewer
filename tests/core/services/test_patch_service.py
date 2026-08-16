from pathlib import Path

import git
import pytest

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus, Repository
from local_changes_viewer.core.services.patch_service import PatchService


@pytest.fixture
def repo(tmp_path: Path) -> git.Repo:
    repo = git.Repo.init(tmp_path, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test User")
        cw.set_value("user", "email", "test@example.com")
    (tmp_path / "committed.txt").write_text("original content\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.txt").write_text("original nested\n")
    repo.index.add(["committed.txt", "sub/nested.txt"])
    repo.index.commit("initial commit")
    return repo


def _make_repository(path: Path, changes: list[FileChange]) -> Repository:
    return Repository(
        path=path,
        name=path.name,
        branch_status=BranchStatus(branch_name="main", ahead=0, behind=0),
        changes=changes,
    )


def test_build_patch_for_whole_repo_includes_every_untracked_file(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "committed.txt").write_text("changed\n")
    (tmp_path / "sub" / "extra.txt").write_text("extra\n")
    repository = _make_repository(
        tmp_path,
        changes=[
            FileChange(path=Path("committed.txt"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("sub/extra.txt"), change_type=ChangeType.UNTRACKED),
        ],
    )

    patch = PatchService().build_patch(repository, Path("."))

    assert "diff --git a/committed.txt b/committed.txt" in patch
    assert "diff --git a/sub/extra.txt b/sub/extra.txt" in patch


def test_build_patch_for_a_folder_excludes_untracked_files_outside_it(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "sub" / "extra.txt").write_text("extra\n")
    (tmp_path / "outside.txt").write_text("outside\n")
    repository = _make_repository(
        tmp_path,
        changes=[
            FileChange(path=Path("sub/extra.txt"), change_type=ChangeType.UNTRACKED),
            FileChange(path=Path("outside.txt"), change_type=ChangeType.UNTRACKED),
        ],
    )

    patch = PatchService().build_patch(repository, Path("sub"))

    assert "sub/extra.txt" in patch
    assert "outside.txt" not in patch


def test_build_patch_for_a_single_file_excludes_an_unrelated_untracked_sibling(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "sub" / "nested.txt").write_text("changed nested\n")
    (tmp_path / "sub" / "sibling.txt").write_text("sibling\n")
    repository = _make_repository(
        tmp_path,
        changes=[
            FileChange(path=Path("sub/nested.txt"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("sub/sibling.txt"), change_type=ChangeType.UNTRACKED),
        ],
    )

    patch = PatchService().build_patch(repository, Path("sub/nested.txt"))

    assert "sub/nested.txt" in patch
    assert "sibling.txt" not in patch


def test_build_patch_returns_empty_string_for_a_clean_repo(tmp_path: Path, repo: git.Repo):
    repository = _make_repository(tmp_path, changes=[])

    patch = PatchService().build_patch(repository, Path("."))

    assert patch == ""
