import os
from pathlib import Path

import pytest

from local_changes_viewer.core.infra.filesystem_scanner import FileSystemScanner


def _make_git_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()


def test_finds_repo_at_root(tmp_path: Path):
    _make_git_dir(tmp_path / "repo_a")

    found = FileSystemScanner().find_git_repos(tmp_path)

    assert found == [tmp_path / "repo_a"]


def test_root_itself_is_a_repo(tmp_path: Path):
    _make_git_dir(tmp_path)

    found = FileSystemScanner().find_git_repos(tmp_path)

    assert found == [tmp_path]


def test_does_not_descend_past_immediate_children(tmp_path: Path):
    _make_git_dir(tmp_path / "level1" / "level2" / "repo_deep")

    found = FileSystemScanner().find_git_repos(tmp_path)

    assert found == []


def test_finds_multiple_sibling_repos(tmp_path: Path):
    _make_git_dir(tmp_path / "repo_a")
    _make_git_dir(tmp_path / "repo_b")

    found = FileSystemScanner().find_git_repos(tmp_path)

    assert found == [tmp_path / "repo_a", tmp_path / "repo_b"]


def test_does_not_look_inside_a_found_repo_for_further_repos(tmp_path: Path):
    outer = tmp_path / "outer_repo"
    inner = outer / "vendor" / "inner_repo"
    _make_git_dir(outer)
    _make_git_dir(inner)

    found = FileSystemScanner().find_git_repos(tmp_path)

    assert found == [outer]


def test_ignores_folders_without_git(tmp_path: Path):
    (tmp_path / "not_a_repo").mkdir()
    (tmp_path / "not_a_repo" / "file.txt").write_text("hello")
    _make_git_dir(tmp_path / "repo_a")

    found = FileSystemScanner().find_git_repos(tmp_path)

    assert found == [tmp_path / "repo_a"]


def test_detects_git_as_file_for_submodules(tmp_path: Path):
    submodule = tmp_path / "submodule_repo"
    submodule.mkdir()
    (submodule / ".git").write_text("gitdir: ../.git/modules/submodule_repo")

    found = FileSystemScanner().find_git_repos(tmp_path)

    assert found == [submodule]


def test_returns_empty_list_when_no_repos_found(tmp_path: Path):
    (tmp_path / "empty_folder").mkdir()

    found = FileSystemScanner().find_git_repos(tmp_path)

    assert found == []


def test_returns_empty_list_rather_than_raising_when_root_itself_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Deterministic stand-in for a workspace root that becomes unreadable
    # (permission change, an unmounted network share) between being chosen
    # and being scanned -- monkeypatched rather than relying on real
    # filesystem permission enforcement, which root/some CI users bypass.
    def _raise_permission_error(self: Path):
        raise PermissionError(f"denied: {self}")

    monkeypatch.setattr(Path, "iterdir", _raise_permission_error)

    found = FileSystemScanner().find_git_repos(tmp_path)

    assert found == []


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="requires POSIX permission enforcement")
def test_skips_unreadable_child_directory_and_still_finds_sibling_repos(tmp_path: Path):
    _make_git_dir(tmp_path / "repo_a")
    unreadable = tmp_path / "no_access"
    unreadable.mkdir()
    os.chmod(unreadable, 0)
    try:
        found = FileSystemScanner().find_git_repos(tmp_path)
    finally:
        # Restore permissions so tmp_path's own cleanup can remove it.
        os.chmod(unreadable, 0o700)

    assert found == [tmp_path / "repo_a"]
