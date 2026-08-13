import shutil
import socket
import threading
import time
from pathlib import Path

import git
import pytest

from local_changes_viewer.core.domain.diff import DiffLineKind
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.core.services import workspace_cache


@pytest.fixture(autouse=True)
def _redirect_default_branch_cache_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every test in this file exercises a fresh GitRepoAdapter, and several
    # exercise _find_default_branch's network path — without this, they'd
    # read/write the real ~/.local-changes-viewer/default_branch_cache.json
    # on whatever machine runs the suite.
    monkeypatch.setattr(
        workspace_cache,
        "_DEFAULT_BRANCH_CACHE_FILE_PATH",
        tmp_path / "cache" / "default_branch_cache.json",
    )


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


def test_list_worktree_details_reports_branch_and_clean_pushed_state(
    tmp_path: Path,
):
    local_path, repo = _init_repo_with_pushed_commit(tmp_path)
    worktree_path = tmp_path / "wt" / "feature-x"
    repo.git.worktree("add", str(worktree_path), "-b", "feature-x")
    wt_repo = git.Repo(worktree_path)
    wt_repo.git.push("--set-upstream", "origin", "feature-x")

    details = GitRepoAdapter(local_path).list_worktree_details()

    assert len(details) == 1
    info = details[0]
    assert info.path == worktree_path
    assert info.branch_name == "feature-x"
    assert info.has_unpushed_changes is False
    assert info.last_activity is not None
    assert info.created_at is not None


def test_list_worktree_details_flags_uncommitted_changes_as_unpushed(tmp_path: Path):
    local_path, repo = _init_repo_with_pushed_commit(tmp_path)
    worktree_path = tmp_path / "wt" / "feature-x"
    repo.git.worktree("add", str(worktree_path), "-b", "feature-x")
    (worktree_path / "committed.txt").write_text("dirty\n")

    details = GitRepoAdapter(local_path).list_worktree_details()

    assert details[0].has_unpushed_changes is True


def test_list_worktree_details_flags_commits_ahead_of_upstream_as_unpushed(tmp_path: Path):
    local_path, repo = _init_repo_with_pushed_commit(tmp_path)
    worktree_path = tmp_path / "wt" / "feature-x"
    repo.git.worktree("add", str(worktree_path), "-b", "feature-x")
    wt_repo = git.Repo(worktree_path)
    wt_repo.git.push("--set-upstream", "origin", "feature-x")
    (worktree_path / "new.txt").write_text("x\n")
    wt_repo.index.add(["new.txt"])
    wt_repo.index.commit("local only commit")

    details = GitRepoAdapter(local_path).list_worktree_details()

    assert details[0].has_unpushed_changes is True


def test_list_worktree_details_skips_worktree_path_that_no_longer_exists_on_disk(
    tmp_path: Path,
):
    local_path, repo = _init_repo_with_pushed_commit(tmp_path)
    worktree_path = tmp_path / "wt" / "feature-x"
    repo.git.worktree("add", str(worktree_path), "-b", "feature-x")
    shutil.rmtree(worktree_path)

    assert GitRepoAdapter(local_path).list_worktree_details() == []


def test_has_unpushed_changes_false_for_clean_pushed_branch(tmp_path: Path):
    local_path, _ = _init_repo_with_pushed_commit(tmp_path)

    assert GitRepoAdapter(local_path).has_unpushed_changes() is False


def test_has_unpushed_changes_true_with_no_upstream_configured(tmp_path: Path, repo: git.Repo):
    assert GitRepoAdapter(tmp_path).has_unpushed_changes() is True


def test_remove_worktree_deletes_it_from_worktree_list(tmp_path: Path, repo: git.Repo):
    worktree_path = tmp_path / "wt" / "feature-x"
    repo.git.worktree("add", str(worktree_path), "-b", "feature-x")

    GitRepoAdapter(tmp_path).remove_worktree(worktree_path)

    assert GitRepoAdapter(tmp_path).list_worktrees() == []
    assert not worktree_path.exists()


def test_remove_worktree_force_removes_worktree_with_uncommitted_changes(
    tmp_path: Path, repo: git.Repo
):
    worktree_path = tmp_path / "wt" / "feature-x"
    repo.git.worktree("add", str(worktree_path), "-b", "feature-x")
    (worktree_path / "dirty.txt").write_text("uncommitted\n")

    with pytest.raises(git.GitCommandError):
        GitRepoAdapter(tmp_path).remove_worktree(worktree_path)

    GitRepoAdapter(tmp_path).remove_worktree(worktree_path, force=True)

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


def test_branch_status_zero_commits_reports_real_branch_name(tmp_path: Path):
    # `git status --porcelain=v1 --branch` prints "## No commits yet on
    # main" (verified against real git output) on a brand-new repo — this
    # used to fall through _BRANCH_LINE_RE and report the literal string
    # "No commits yet on main" as the branch name.
    git.Repo.init(tmp_path, initial_branch="main")

    status = GitRepoAdapter(tmp_path).get_branch_status()

    assert status.branch_name == "main"
    assert status.ahead == 0
    assert status.behind == 0


def test_branch_status_detached_head(tmp_path: Path, repo: git.Repo):
    repo.git.checkout(repo.head.commit.hexsha)

    status = GitRepoAdapter(tmp_path).get_branch_status()

    assert status.branch_name == "HEAD"


def test_find_default_branch_falls_back_to_local_symref_when_remote_unreachable(
    tmp_path: Path,
):
    # refs/remotes/origin/HEAD is a local symbolic ref set by `git remote
    # set-head` (or a real `git clone`) — it is NOT authoritative (see the
    # disagreement test below), so it must only be used once both the cache
    # and a live network probe have failed. Proven here by pointing origin
    # at a URL that cannot be resolved: the network probe fails, and the
    # stale-but-still-correct local symref is what's left to fall back to.
    remote_bare = tmp_path / "remote.git"
    git.Repo.init(remote_bare, bare=True)

    local_path = tmp_path / "local_repo"
    repo = _init_repo_with_commit(local_path)
    repo.create_remote("origin", str(remote_bare))
    repo.git.push("--set-upstream", "origin", "main")
    repo.git.remote("set-head", "origin", "main")

    # Break reachability after the local symref is already recorded.
    repo.git.remote("set-url", "origin", "https://127.0.0.1.invalid/unreachable.git")

    status = GitRepoAdapter(local_path).get_branch_status()

    assert status.default_branch == "main"


def test_find_default_branch_prefers_authoritative_remote_over_stale_local_symref(
    tmp_path: Path,
):
    # Regression test for the exact bug just found on the user's real
    # repos: refs/remotes/origin/HEAD is written once at clone time and is
    # NOT refreshed by `git fetch` — only by an explicit
    # `git remote set-head origin -a`. So it can silently disagree with the
    # remote's actual current HEAD. When it does, the live (or cached)
    # answer from ls-remote must win, never the stale local symref.
    remote_bare = tmp_path / "remote.git"
    remote_repo = git.Repo.init(remote_bare, bare=True)

    local_path = tmp_path / "local_repo"
    repo = _init_repo_with_commit(local_path)
    repo.create_remote("origin", str(remote_bare))
    repo.git.push("--set-upstream", "origin", "main")
    repo.git.branch("develop")
    repo.git.push("origin", "develop")

    # The remote's own HEAD (what `ls-remote --symref origin HEAD` reports)
    # now really points at "develop" — e.g. someone repointed the repo's
    # default branch on the server after this clone was made.
    remote_repo.git.symbolic_ref("HEAD", "refs/heads/develop")

    repo.git.fetch("origin")
    # Force the local mirror to disagree with the remote: simulates a clone
    # made back when "main" was still the default, never refreshed since.
    repo.git.symbolic_ref("refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    status = GitRepoAdapter(local_path).get_branch_status()

    assert status.default_branch == "develop"


def test_find_default_branch_falls_back_to_ls_remote_when_symref_missing(tmp_path: Path):
    # No local refs/remotes/origin/HEAD (never `set-head`'d) — the code
    # must fall back to asking the remote directly and still get the right
    # answer.
    remote_bare = tmp_path / "remote.git"
    git.Repo.init(remote_bare, bare=True)

    local_path = tmp_path / "local_repo"
    repo = _init_repo_with_commit(local_path)
    repo.create_remote("origin", str(remote_bare))
    repo.git.push("--set-upstream", "origin", "main")

    status = GitRepoAdapter(local_path).get_branch_status()

    assert status.default_branch == "main"


def test_find_default_branch_none_when_no_origin_remote(
    tmp_path: Path, repo: git.Repo, monkeypatch: pytest.MonkeyPatch
):
    # Isolate from this machine's system-level gitconfig: Apple's Command
    # Line Tools ship one that sets init.defaultbranch=main, which `git
    # config init.defaultBranch` (the final fallback) would otherwise pick
    # up and mask the "nothing determinable" case under test here.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    status = GitRepoAdapter(tmp_path).get_branch_status()

    assert status.default_branch is None


def test_find_default_branch_memoizes_across_calls(
    tmp_path: Path, repo: git.Repo, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # Deliberately not "main": the system-level gitconfig's
    # init.defaultbranch=main would make an unmemoized second call return
    # the same value by coincidence, hiding a broken cache.
    repo.git.config("init.defaultBranch", "trunk")
    adapter = GitRepoAdapter(tmp_path)

    first = adapter._find_default_branch()
    # Delete the config the first call relied on: a second, non-memoized
    # lookup would now return None instead of the cached "trunk".
    repo.git.config("--unset", "init.defaultBranch")
    second = adapter._find_default_branch()

    assert first == "trunk"
    assert second == "trunk"


def test_find_default_branch_bounded_when_remote_never_responds(
    tmp_path: Path, repo: git.Repo, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    # A remote that accepts the TCP connection but never speaks the git
    # protocol reproduces the original hang (no local symref, network call
    # blocks forever with no timeout). This proves the fix is genuinely
    # bounded rather than just "usually fast".
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(1)
    port = server_socket.getsockname()[1]

    def _accept_and_stall() -> None:
        try:
            conn, _ = server_socket.accept()
            time.sleep(30)
            conn.close()
        except OSError:
            pass

    server_thread = threading.Thread(target=_accept_and_stall, daemon=True)
    server_thread.start()
    try:
        repo.create_remote("origin", f"git://127.0.0.1:{port}/repo.git")

        started = time.monotonic()
        status = GitRepoAdapter(tmp_path).get_branch_status()
        elapsed = time.monotonic() - started

        assert elapsed < 15, "default branch lookup did not honor its timeout"
        assert status.default_branch is None
    finally:
        server_socket.close()


def test_branch_status_finds_parent_branch_with_slash_in_name(tmp_path: Path, repo: git.Repo):
    # Branch names containing '/' are common here (e.g. "TASK/foo-bar") —
    # the parent-branch lookup must not split on '/' anywhere in its ref
    # handling.
    repo.git.checkout("-b", "TASK/foo-bar")
    (tmp_path / "feature_file.txt").write_text("feature work\n")
    repo.index.add(["feature_file.txt"])
    repo.index.commit("feature commit")

    status = GitRepoAdapter(tmp_path).get_branch_status()

    assert status.branch_name == "TASK/foo-bar"
    assert status.parent_branch == "main"


def test_find_local_parent_branch_ignores_unrelated_orphan_branch(tmp_path: Path, repo: git.Repo):
    # An orphan branch shares no history with `current` at all, so its
    # merge-base is empty and it must be skipped rather than crashing or
    # winning by default.
    repo.git.checkout("--orphan", "unrelated")
    (tmp_path / "orphan_file.txt").write_text("orphan\n")
    repo.index.add(["orphan_file.txt"])
    repo.index.commit("orphan root commit")
    repo.git.checkout("main")

    repo.git.branch("main-line")
    repo.git.checkout("-b", "feature")
    (tmp_path / "feature_file.txt").write_text("feature work\n")
    repo.index.add(["feature_file.txt"])
    repo.index.commit("feature commit")

    status = GitRepoAdapter(tmp_path).get_branch_status()

    assert status.parent_branch in ("main", "main-line")


def test_find_local_parent_branch_matches_git_merge_base_across_merge_commit(
    tmp_path: Path, repo: git.Repo
):
    # Regression test for the graph-based reimplementation: with a real
    # merge commit in the history, the "most recent common ancestor" must
    # still match what `git merge-base` itself would report, not just the
    # newest-by-date node anywhere in the shared history. Commit dates are
    # pinned explicitly (a fast test run can otherwise create several
    # commits within the same wall-clock second, making the "most recent"
    # comparison a coin flip on committed_date's 1-second resolution).
    def _commit(message: str, offset_seconds: int) -> None:
        date = f"2024-01-01T00:00:{offset_seconds:02d} +0000"
        repo.index.commit(message, commit_date=date, author_date=date)

    repo.git.checkout("-b", "feature")
    (tmp_path / "feat.txt").write_text("x\n")
    repo.index.add(["feat.txt"])
    _commit("feature work", 10)

    repo.git.checkout("main")
    (tmp_path / "main2.txt").write_text("y\n")
    repo.index.add(["main2.txt"])
    _commit("main work", 20)
    repo.git.merge("feature", "--no-ff", "-m", "merge feature")

    repo.git.checkout("-b", "topic", "HEAD~1")  # branches off pre-merge main
    (tmp_path / "topic.txt").write_text("z\n")
    repo.index.add(["topic.txt"])
    _commit("topic work", 40)
    repo.git.checkout("main")

    adapter = GitRepoAdapter(tmp_path)
    # Ground truth from real `git merge-base`, to compare the new
    # graph-based computation against.
    real_merge_base = adapter._repo.git.merge_base("main", "topic")

    parent = adapter._find_local_parent_branch("main")

    # merge-base(main, feature) is "feature work" (date 10); merge-base(main,
    # topic) is "main work" (date 20, main's own pre-merge tip) — the more
    # recent of the two, so "topic" must win.
    assert parent == "topic"
    assert real_merge_base == adapter._repo.heads["main"].commit.parents[0].hexsha
