from pathlib import Path

import git
import pytest

from local_changes_viewer.core.domain.file_change import ChangeType
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter


def _init_repo_with_commit(path: Path) -> git.Repo:
    repo = git.Repo.init(path, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test User")
        cw.set_value("user", "email", "test@example.com")
    (path / "committed.txt").write_text("original content\n")
    repo.index.add(["committed.txt"])
    repo.index.commit("initial commit")
    return repo


@pytest.fixture
def repo(tmp_path: Path) -> git.Repo:
    return _init_repo_with_commit(tmp_path)


def test_list_changes_detects_modified_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("changed content\n")

    changes = GitRepoAdapter(tmp_path).list_changes()

    assert any(
        c.path == Path("committed.txt") and c.change_type == ChangeType.MODIFIED
        for c in changes
    )


def test_list_changes_detects_untracked_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "new_file.txt").write_text("new\n")

    changes = GitRepoAdapter(tmp_path).list_changes()

    assert any(
        c.path == Path("new_file.txt") and c.change_type == ChangeType.UNTRACKED
        for c in changes
    )


def test_list_changes_detects_added_staged_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "staged_file.txt").write_text("staged\n")
    repo.index.add(["staged_file.txt"])

    changes = GitRepoAdapter(tmp_path).list_changes()

    assert any(
        c.path == Path("staged_file.txt") and c.change_type == ChangeType.ADDED
        for c in changes
    )


def test_list_changes_detects_deleted_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").unlink()

    changes = GitRepoAdapter(tmp_path).list_changes()

    assert any(
        c.path == Path("committed.txt") and c.change_type == ChangeType.DELETED
        for c in changes
    )


def test_list_changes_detects_renamed_staged_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").rename(tmp_path / "renamed.txt")
    repo.index.remove(["committed.txt"])
    repo.index.add(["renamed.txt"])

    changes = GitRepoAdapter(tmp_path).list_changes()

    match = next(c for c in changes if c.path == Path("renamed.txt"))
    assert match.change_type == ChangeType.RENAMED
    assert match.old_path == Path("committed.txt")


def test_list_changes_detects_ignored_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / ".gitignore").write_text("ignored_file.txt\n")
    repo.index.add([".gitignore"])
    repo.index.commit("add gitignore")
    (tmp_path / "ignored_file.txt").write_text("ignored\n")

    changes = GitRepoAdapter(tmp_path).list_changes()

    assert any(
        c.path == Path("ignored_file.txt") and c.change_type == ChangeType.IGNORED
        for c in changes
    )


def test_list_changes_empty_for_clean_repo(tmp_path: Path, repo: git.Repo):
    changes = GitRepoAdapter(tmp_path).list_changes()

    assert changes == []


def test_branch_status_with_no_upstream(tmp_path: Path, repo: git.Repo):
    status = GitRepoAdapter(tmp_path).get_branch_status()

    assert status.branch_name == "main"
    assert status.ahead == 0
    assert status.behind == 0


def test_branch_status_ahead_and_behind(tmp_path: Path):
    remote_bare = tmp_path / "remote.git"
    git.Repo.init(remote_bare, bare=True)

    local_path = tmp_path / "local_repo"
    repo = _init_repo_with_commit(local_path)
    repo.create_remote("origin", str(remote_bare))
    repo.git.push("--set-upstream", "origin", "main")

    other_clone_path = tmp_path / "other_clone"
    other_repo = git.Repo.clone_from(str(remote_bare), other_clone_path)
    with other_repo.config_writer() as cw:
        cw.set_value("user", "name", "Test User")
        cw.set_value("user", "email", "test@example.com")
    (other_clone_path / "committed.txt").write_text("advanced by someone else\n")
    other_repo.index.add(["committed.txt"])
    other_repo.index.commit("advance origin")
    other_repo.git.push("origin", "main")

    (local_path / "local_only.txt").write_text("local commit\n")
    repo.index.add(["local_only.txt"])
    repo.index.commit("local ahead commit")

    repo.git.fetch("origin")

    status = GitRepoAdapter(local_path).get_branch_status()

    assert status.branch_name == "main"
    assert status.ahead == 1
    assert status.behind == 1
