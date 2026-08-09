from pathlib import Path

import git
import pytest

from local_changes_viewer.core.domain.diff import DiffLineKind
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
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

    match = next(c for c in changes if c.path == Path("new_file.txt"))
    assert match.change_type == ChangeType.UNTRACKED
    assert match.is_directory is False


def test_list_changes_detects_untracked_directory_as_single_directory_entry(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "new_dir").mkdir()
    (tmp_path / "new_dir" / "a.txt").write_text("a\n")
    (tmp_path / "new_dir" / "b.txt").write_text("b\n")

    changes = GitRepoAdapter(tmp_path).list_changes()

    assert [c.path for c in changes] == [Path("new_dir")]
    match = changes[0]
    assert match.change_type == ChangeType.UNTRACKED
    assert match.is_directory is True


def test_list_changes_classifies_symlinked_directory_as_directory(
    tmp_path: Path, repo: git.Repo
):
    # git status never descends into a symlink, even one pointing at a
    # directory (e.g. a symlinked node_modules) — it reports the symlink's
    # own path with no trailing slash, the same shape as an untracked file.
    # Without stat-ing it, a folder-filter rule like equals:'node_modules'
    # would never match it (see workspace_filter._is_inside_filtered_folder).
    real_dir = tmp_path.parent / "external_node_modules"
    real_dir.mkdir()
    (tmp_path / "node_modules").symlink_to(real_dir, target_is_directory=True)

    changes = GitRepoAdapter(tmp_path).list_changes()

    match = next(c for c in changes if c.path == Path("node_modules"))
    assert match.change_type == ChangeType.UNTRACKED
    assert match.is_directory is True


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


def test_compute_diff_for_modified_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("original CONTENT\n")
    change = FileChange(path=Path("committed.txt"), change_type=ChangeType.MODIFIED)

    result = GitRepoAdapter(tmp_path).compute_diff(change)

    assert len(result.hunks) == 1
    lines = result.hunks[0].lines
    assert [line.kind for line in lines] == [DiffLineKind.REMOVED, DiffLineKind.ADDED]
    assert lines[0].text == "original content"
    assert lines[1].text == "original CONTENT"


def test_compute_diff_for_deleted_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").unlink()
    change = FileChange(path=Path("committed.txt"), change_type=ChangeType.DELETED)

    result = GitRepoAdapter(tmp_path).compute_diff(change)

    assert len(result.hunks) == 1
    lines = result.hunks[0].lines
    assert all(line.kind == DiffLineKind.REMOVED for line in lines)
    assert lines[0].text == "original content"


def test_compute_diff_for_untracked_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "new_file.txt").write_text("line one\nline two\n")
    change = FileChange(path=Path("new_file.txt"), change_type=ChangeType.UNTRACKED)

    result = GitRepoAdapter(tmp_path).compute_diff(change)

    assert len(result.hunks) == 1
    lines = result.hunks[0].lines
    assert [line.text for line in lines] == ["line one", "line two"]
    assert all(line.kind == DiffLineKind.ADDED for line in lines)
    assert [line.new_lineno for line in lines] == [1, 2]


def test_compute_diff_for_renamed_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "wide.txt").write_text("a\nb\nc\nd\ne\n")
    repo.index.add(["wide.txt"])
    repo.index.commit("add wide.txt")

    (tmp_path / "wide.txt").rename(tmp_path / "renamed.txt")
    (tmp_path / "renamed.txt").write_text("a\nB\nc\nd\ne\n")
    repo.index.remove(["wide.txt"])
    repo.index.add(["renamed.txt"])
    change = FileChange(
        path=Path("renamed.txt"), change_type=ChangeType.RENAMED, old_path=Path("wide.txt")
    )

    result = GitRepoAdapter(tmp_path).compute_diff(change)

    assert len(result.hunks) == 1
    lines = result.hunks[0].lines
    assert any(line.kind == DiffLineKind.REMOVED and line.text == "b" for line in lines)
    assert any(line.kind == DiffLineKind.ADDED and line.text == "B" for line in lines)


def test_compute_diff_ignore_whitespace(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("original    content\n")
    change = FileChange(path=Path("committed.txt"), change_type=ChangeType.MODIFIED)

    result = GitRepoAdapter(tmp_path).compute_diff(change, ignore_whitespace=True)

    assert result.hunks == []


def test_branch_status_finds_local_parent_branch(tmp_path: Path, repo: git.Repo):
    repo.git.branch("main-line")

    repo.git.checkout("-b", "feature")
    (tmp_path / "feature_file.txt").write_text("feature work\n")
    repo.index.add(["feature_file.txt"])
    repo.index.commit("feature commit")

    status = GitRepoAdapter(tmp_path).get_branch_status()

    assert status.branch_name == "feature"
    assert status.parent_branch in ("main", "main-line")


def test_branch_status_parent_branch_none_when_no_other_branches(tmp_path: Path, repo: git.Repo):
    status = GitRepoAdapter(tmp_path).get_branch_status()

    assert status.parent_branch is None


def test_branch_status_default_branch_falls_back_to_init_default_branch_config(
    tmp_path: Path, repo: git.Repo
):
    repo.git.config("init.defaultBranch", "main")

    status = GitRepoAdapter(tmp_path).get_branch_status()

    assert status.default_branch == "main"


def test_get_remote_url_returns_none_when_no_remote(tmp_path: Path, repo: git.Repo):
    assert GitRepoAdapter(tmp_path).get_remote_url() is None


def test_get_remote_url_returns_origin_url(tmp_path: Path, repo: git.Repo):
    repo.create_remote("origin", "https://github.com/owner/repo.git")

    assert GitRepoAdapter(tmp_path).get_remote_url() == "https://github.com/owner/repo.git"


def test_list_worktrees_returns_linked_worktree_paths(tmp_path: Path, repo: git.Repo):
    worktree_path = tmp_path / "wt" / "feature-x"
    repo.git.worktree("add", str(worktree_path), "-b", "feature-x")

    worktrees = GitRepoAdapter(tmp_path).list_worktrees()

    assert worktrees == [worktree_path]


def test_list_worktrees_returns_empty_list_when_no_linked_worktrees(tmp_path: Path, repo: git.Repo):
    assert GitRepoAdapter(tmp_path).list_worktrees() == []


def test_branch_status_default_branch_queried_live_from_remote(tmp_path: Path):
    remote_bare = tmp_path / "remote.git"
    git.Repo.init(remote_bare, bare=True)

    local_path = tmp_path / "local_repo"
    repo = _init_repo_with_commit(local_path)
    repo.create_remote("origin", str(remote_bare))
    repo.git.push("--set-upstream", "origin", "main")
    repo.git.remote("set-head", "origin", "main")

    status = GitRepoAdapter(local_path).get_branch_status()

    assert status.default_branch == "main"


def _init_repo_with_pushed_commit(tmp_path: Path) -> tuple[Path, git.Repo]:
    remote_bare = tmp_path / "remote.git"
    git.Repo.init(remote_bare, bare=True)

    local_path = tmp_path / "local_repo"
    repo = _init_repo_with_commit(local_path)
    repo.create_remote("origin", str(remote_bare))
    repo.git.push("--set-upstream", "origin", "main")
    return local_path, repo


def test_list_changes_excludes_unpushed_commit_by_default(tmp_path: Path):
    local_path, repo = _init_repo_with_pushed_commit(tmp_path)
    (local_path / "committed.txt").write_text("changed but committed\n")
    repo.index.add(["committed.txt"])
    repo.index.commit("local only commit")

    changes = GitRepoAdapter(local_path).list_changes()

    assert changes == []


def test_list_changes_includes_unpushed_commit_when_requested(tmp_path: Path):
    local_path, repo = _init_repo_with_pushed_commit(tmp_path)
    (local_path / "committed.txt").write_text("changed but committed\n")
    repo.index.add(["committed.txt"])
    repo.index.commit("local only commit")

    changes = GitRepoAdapter(local_path).list_changes(include_unpushed_commits=True)

    match = next(c for c in changes if c.path == Path("committed.txt"))
    assert match.change_type == ChangeType.MODIFIED
    assert match.is_unpushed_commit is True


def test_list_changes_includes_commit_message_for_unpushed_commit(tmp_path: Path):
    local_path, repo = _init_repo_with_pushed_commit(tmp_path)
    (local_path / "committed.txt").write_text("changed but committed\n")
    repo.index.add(["committed.txt"])
    repo.index.commit("local only commit")

    changes = GitRepoAdapter(local_path).list_changes(include_unpushed_commits=True)

    match = next(c for c in changes if c.path == Path("committed.txt"))
    assert match.commit_message == "local only commit"


def test_list_changes_does_not_duplicate_file_already_dirty_in_working_tree(tmp_path: Path):
    local_path, repo = _init_repo_with_pushed_commit(tmp_path)
    (local_path / "committed.txt").write_text("committed change\n")
    repo.index.add(["committed.txt"])
    repo.index.commit("local only commit")
    (local_path / "committed.txt").write_text("uncommitted change on top\n")

    changes = GitRepoAdapter(local_path).list_changes(include_unpushed_commits=True)

    matches = [c for c in changes if c.path == Path("committed.txt")]
    assert len(matches) == 1
    assert matches[0].is_unpushed_commit is False


def test_list_changes_returns_no_unpushed_commits_when_branch_has_no_upstream(tmp_path: Path):
    repo = _init_repo_with_commit(tmp_path)
    (tmp_path / "committed.txt").write_text("changed but committed\n")
    repo.index.add(["committed.txt"])
    repo.index.commit("local only commit")

    changes = GitRepoAdapter(tmp_path).list_changes(include_unpushed_commits=True)

    assert changes == []


def test_compute_diff_for_unpushed_commit_diffs_against_upstream(tmp_path: Path):
    local_path, repo = _init_repo_with_pushed_commit(tmp_path)
    (local_path / "committed.txt").write_text("changed but committed\n")
    repo.index.add(["committed.txt"])
    repo.index.commit("local only commit")

    change = FileChange(
        path=Path("committed.txt"),
        change_type=ChangeType.MODIFIED,
        is_unpushed_commit=True,
    )
    diff = GitRepoAdapter(local_path).compute_diff(change)

    assert diff.new_ref == "HEAD"
    added_lines = [
        line.text
        for hunk in diff.hunks
        for line in hunk.lines
        if line.kind == DiffLineKind.ADDED
    ]
    assert "changed but committed" in added_lines


def test_get_recent_commits_returns_newest_first_with_limit(tmp_path: Path, repo: git.Repo):
    for i in range(3):
        (tmp_path / "committed.txt").write_text(f"content {i}\n")
        repo.index.add(["committed.txt"])
        repo.index.commit(f"commit {i}")

    commits = GitRepoAdapter(tmp_path).get_recent_commits(limit=2)

    assert [c.message for c in commits] == ["commit 2", "commit 1"]
    assert all(len(c.short_hexsha) == 8 for c in commits)


def test_get_commit_files_lists_changed_paths(tmp_path: Path, repo: git.Repo):
    (tmp_path / "new_file.txt").write_text("new\n")
    repo.index.add(["new_file.txt"])
    commit = repo.index.commit("add new file")

    changes = GitRepoAdapter(tmp_path).get_commit_files(commit.hexsha)

    assert any(
        c.path == Path("new_file.txt") and c.change_type == ChangeType.ADDED for c in changes
    )


def test_get_commit_file_diff_shows_added_content(tmp_path: Path, repo: git.Repo):
    (tmp_path / "new_file.txt").write_text("hello\n")
    repo.index.add(["new_file.txt"])
    commit = repo.index.commit("add new file")

    diff = GitRepoAdapter(tmp_path).get_commit_file_diff(commit.hexsha, Path("new_file.txt"))

    added_lines = [
        line.text
        for hunk in diff.hunks
        for line in hunk.lines
        if line.kind == DiffLineKind.ADDED
    ]
    assert "hello" in added_lines
