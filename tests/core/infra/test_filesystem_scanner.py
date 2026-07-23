from pathlib import Path

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
