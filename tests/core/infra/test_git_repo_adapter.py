import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import git
import pytest

from local_changes_viewer.core.domain.diff import DiffLineKind
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.infra import git_repo_adapter
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


def test_list_changes_and_diff_round_trip_a_non_ascii_filename(tmp_path: Path, repo: git.Repo):
    # Regression test: git's default core.quotePath=true C-quotes a
    # non-ASCII filename into an escaped literal like "\327\251..." in the
    # old newline-splitting parser's input, so the FileChange this produced
    # never matched the real file on disk -- a later `git diff -- <path>`
    # against that escaped string matched nothing, and the user saw the
    # file listed with an empty diff. `-z` + `-c core.quotePath=false` must
    # round-trip the real UTF-8 name end to end.
    (tmp_path / "שלום.txt").write_text("hello\n", encoding="utf-8")

    changes = GitRepoAdapter(tmp_path).list_changes()

    match = next(c for c in changes if c.path == Path("שלום.txt"))
    assert match.change_type == ChangeType.UNTRACKED

    diff = GitRepoAdapter(tmp_path).compute_diff(match)
    lines = [line.text for hunk in diff.hunks for line in hunk.lines]
    assert "hello" in lines


def test_compute_diff_for_modified_non_ascii_filename_matches_real_content(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "תודה.txt").write_text("original\n", encoding="utf-8")
    repo.index.add(["תודה.txt"])
    repo.index.commit("add hebrew file")
    (tmp_path / "תודה.txt").write_text("changed\n", encoding="utf-8")

    changes = GitRepoAdapter(tmp_path).list_changes()
    match = next(c for c in changes if c.path == Path("תודה.txt"))
    assert match.change_type == ChangeType.MODIFIED

    diff = GitRepoAdapter(tmp_path).compute_diff(match)
    # Before the fix, `git diff -- <mis-parsed escaped path>` matched
    # nothing, so this came back with zero hunks instead of the real edit.
    assert len(diff.hunks) == 1
    added = [line.text for line in diff.hunks[0].lines if line.kind == DiffLineKind.ADDED]
    assert added == ["changed"]


def test_list_changes_detects_renamed_file_with_non_ascii_names(tmp_path: Path, repo: git.Repo):
    (tmp_path / "מסמך.txt").write_text("a\nb\nc\n", encoding="utf-8")
    repo.index.add(["מסמך.txt"])
    repo.index.commit("add hebrew file")
    (tmp_path / "מסמך.txt").rename(tmp_path / "קובץ_חדש.txt")
    repo.index.remove(["מסמך.txt"])
    repo.index.add(["קובץ_חדש.txt"])

    changes = GitRepoAdapter(tmp_path).list_changes()

    match = next(c for c in changes if c.path == Path("קובץ_חדש.txt"))
    assert match.change_type == ChangeType.RENAMED
    assert match.old_path == Path("מסמך.txt")


def test_list_changes_detects_renamed_file_whose_name_contains_arrow_literal(
    tmp_path: Path, repo: git.Repo
):
    # The old parser split each status line on the literal substring
    # " -> " to separate a rename's old/new paths -- a filename that itself
    # contains that exact substring would corrupt the split. `-z` sidesteps
    # this entirely: old and new paths are separate NUL-terminated records,
    # never joined with " -> " text at all.
    (tmp_path / "committed.txt").rename(tmp_path / "a -> b.txt")
    repo.index.remove(["committed.txt"])
    repo.index.add(["a -> b.txt"])

    changes = GitRepoAdapter(tmp_path).list_changes()

    match = next(c for c in changes if c.path == Path("a -> b.txt"))
    assert match.change_type == ChangeType.RENAMED
    assert match.old_path == Path("committed.txt")


def test_get_commit_files_reports_real_non_ascii_path(tmp_path: Path, repo: git.Repo):
    (tmp_path / "לקוח.txt").write_text("x\n", encoding="utf-8")
    repo.index.add(["לקוח.txt"])
    commit = repo.index.commit("add hebrew file")

    changes = GitRepoAdapter(tmp_path).get_commit_files(commit.hexsha)

    assert any(c.path == Path("לקוח.txt") for c in changes)


def test_list_changes_includes_unpushed_commit_with_non_ascii_path(tmp_path: Path):
    local_path, repo = _init_repo_with_pushed_commit(tmp_path)
    (local_path / "עדכון.txt").write_text("x\n", encoding="utf-8")
    repo.index.add(["עדכון.txt"])
    repo.index.commit("local only hebrew file")

    changes = GitRepoAdapter(local_path).list_changes(include_unpushed_commits=True)

    match = next(c for c in changes if c.path == Path("עדכון.txt"))
    assert match.is_unpushed_commit is True


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


def test_parse_unified_diff_is_public_and_parses_a_single_file_chunk(
    tmp_path: Path, repo: git.Repo
):
    # Public entry point used by StashesDialog to turn one file's chunk of a
    # multi-file stash patch (from PatchService.split_patch) into the same
    # DiffResult shape compute_diff produces -- no private staticmethod
    # reach-in needed.
    (tmp_path / "committed.txt").write_text("original content\nsecond line\nthird line\n")
    repo.index.add(["committed.txt"])
    repo.index.commit("expand")
    (tmp_path / "committed.txt").write_text("original content\nSECOND LINE\nthird line\n")
    raw = repo.git.diff("--no-color", "HEAD", "--", "committed.txt")

    result = GitRepoAdapter.parse_unified_diff(raw, old_ref="HEAD", new_ref="working tree")

    assert result.old_ref == "HEAD"
    assert result.new_ref == "working tree"
    assert len(result.hunks) == 1
    lines = result.hunks[0].lines
    assert [line.kind for line in lines] == [
        DiffLineKind.CONTEXT,
        DiffLineKind.REMOVED,
        DiffLineKind.ADDED,
        DiffLineKind.CONTEXT,
    ]
    assert lines[1].text == "second line"
    assert lines[2].text == "SECOND LINE"


def test_compute_diff_for_untracked_directory_does_not_crash(tmp_path: Path, repo: git.Repo):
    # Real trigger (user report): `git status` collapses an untracked
    # directory (e.g. node_modules) into one porcelain entry with a trailing
    # slash rather than one line per file inside it, so the FileChange this
    # produces names the directory itself. Reading that path as if it were a
    # file text-content raised IsADirectoryError: [Errno 21].
    (tmp_path / "new_dir").mkdir()
    (tmp_path / "new_dir" / "a.txt").write_text("a\n")
    (tmp_path / "new_dir" / "b.txt").write_text("b\n")
    change = FileChange(path=Path("new_dir"), change_type=ChangeType.UNTRACKED, is_directory=True)

    result = GitRepoAdapter(tmp_path).compute_diff(change)

    lines = [line.text for line in result.hunks[0].lines]
    assert any("2 file" in line for line in lines)
    assert any(line.strip() == "a.txt" for line in lines)
    assert any(line.strip() == "b.txt" for line in lines)


def test_compute_diff_for_untracked_binary_file_shows_placeholder(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "image.bin").write_bytes(b"\x00\x01\x02not-utf8\xff\xfe")
    change = FileChange(path=Path("image.bin"), change_type=ChangeType.UNTRACKED)

    result = GitRepoAdapter(tmp_path).compute_diff(change)

    lines = [line.text for line in result.hunks[0].lines]
    assert any("Binary file" in line for line in lines)


def test_compute_diff_for_untracked_file_over_size_cap_shows_placeholder(
    tmp_path: Path, repo: git.Repo, monkeypatch: pytest.MonkeyPatch
):
    # Lowering the cap (rather than writing a real multi-megabyte file to
    # disk) exercises the same code path -- "stop before read_bytes()" --
    # without a slow, disk-heavy test.
    monkeypatch.setattr(git_repo_adapter, "_UNTRACKED_FILE_DIFF_MAX_BYTES", 10)
    (tmp_path / "big.txt").write_text("0123456789ABCDEF\n")
    change = FileChange(path=Path("big.txt"), change_type=ChangeType.UNTRACKED)

    result = GitRepoAdapter(tmp_path).compute_diff(change)

    lines = [line.text for line in result.hunks[0].lines]
    assert any("too large" in line.lower() for line in lines)
    assert not any("0123456789" in line for line in lines)


def test_compute_diff_for_untracked_file_deleted_after_scan(tmp_path: Path, repo: git.Repo):
    # The scan that produced this FileChange can race a concurrent delete
    # (or an app-triggered worktree cleanup) -- the file must be gone by the
    # time compute_diff actually reads it, without a crash.
    change = FileChange(path=Path("vanished.txt"), change_type=ChangeType.UNTRACKED)

    result = GitRepoAdapter(tmp_path).compute_diff(change)

    lines = [line.text for line in result.hunks[0].lines]
    assert any("no longer exists" in line for line in lines)


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


def test_has_unpushed_changes_false_when_only_ignored_paths_are_present(tmp_path: Path):
    local_path, _ = _init_repo_with_pushed_commit(tmp_path)
    (local_path / ".gitignore").write_text("node_modules/\n")
    local_repo = git.Repo(local_path)
    local_repo.index.add([".gitignore"])
    local_repo.index.commit("ignore node_modules")
    local_repo.git.push("origin", "HEAD")
    (local_path / "node_modules").mkdir()
    (local_path / "node_modules" / "pkg.js").write_text("x\n")

    adapter = GitRepoAdapter(local_path)

    # The file views drop ignored entries, so counting them here is what
    # produced "Unpushed Changes: Yes" over an empty Files Changed list.
    assert adapter.list_changes(include_unpushed_commits=True) != []
    assert adapter.has_unpushed_changes() is False


def test_has_unpushed_changes_true_with_no_upstream_configured(tmp_path: Path, repo: git.Repo):
    assert GitRepoAdapter(tmp_path).has_unpushed_changes() is True


def test_list_changes_reports_local_only_commits_with_no_upstream_configured(
    tmp_path: Path, repo: git.Repo
):
    repo.git.checkout("-b", "feature-x")
    (tmp_path / "new.txt").write_text("x\n")
    repo.index.add(["new.txt"])
    repo.index.commit("local only commit")

    adapter = GitRepoAdapter(tmp_path)
    changes = adapter.list_changes(include_unpushed_commits=True)

    assert adapter.has_unpushed_changes() is True
    matching = [c for c in changes if c.path == Path("new.txt")]
    assert len(matching) == 1
    assert matching[0].is_unpushed_commit is True

    diff = adapter.compute_diff(matching[0])
    assert any(line.text == "x" for hunk in diff.hunks for line in hunk.lines)


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


def test_get_recent_commits_reports_current_branch_over_alphabetically_first(
    tmp_path: Path, repo: git.Repo
):
    # `git branch --contains <sha> --format=%(refname:short)` prints matches
    # in plain alphabetical order. "main" sorts before "zzz-current", so the
    # old code always reported "main" here regardless of which branch is
    # actually checked out -- even though the commit is being viewed from
    # "zzz-current" right now.
    repo.git.checkout("-b", "zzz-current")

    commits = GitRepoAdapter(tmp_path).get_recent_commits(limit=1)

    assert commits[0].branch_name == "zzz-current"


def test_get_recent_commits_reports_default_branch_when_current_branch_unknown(tmp_path: Path):
    # Detached HEAD (no "current branch" to prefer) -- the next
    # least-surprising answer is the repo's default branch, not whichever
    # name happens to sort first alphabetically ("aaa-first" here).
    remote_bare = tmp_path / "remote.git"
    git.Repo.init(remote_bare, bare=True)
    local_path = tmp_path / "local_repo"
    repo = _init_repo_with_commit(local_path)
    repo.create_remote("origin", str(remote_bare))
    repo.git.push("--set-upstream", "origin", "main")
    repo.git.remote("set-head", "origin", "main")
    repo.git.branch("aaa-first")
    repo.git.checkout(repo.head.commit.hexsha)

    commits = GitRepoAdapter(local_path).get_recent_commits(limit=1)

    assert commits[0].branch_name == "main"


def test_get_branch_for_commit_falls_back_to_alphabetically_first_when_no_preference_matches(
    tmp_path: Path, repo: git.Repo, monkeypatch: pytest.MonkeyPatch
):
    # Neither a current branch (detached HEAD) nor a default branch (no
    # remote configured, and the system-level gitconfig isolated out) is
    # available to prefer -- this is the last-resort fallback, unchanged
    # from before the fix.
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo.git.branch("aaa-branch")
    repo.git.branch("zzz-branch")
    commit = repo.head.commit.hexsha
    repo.git.checkout(commit)

    branch_name = GitRepoAdapter(tmp_path)._get_branch_for_commit(commit)

    assert branch_name == "aaa-branch"


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


def test_ls_remote_default_branch_timeout_returns_none_without_spawning_ps(
    tmp_path: Path, repo: git.Repo
):
    # Regression test for the macOS `ps: illegal option -- -` noise:
    # GitPython's `kill_after_timeout=` watchdog used to shell out to
    # `ps --ppid <pid>` to find the child to kill, and `--ppid` is a
    # GNU/Linux-only ps flag that BSD/macOS ps rejects. The fix runs git
    # itself via subprocess.run(timeout=...), which never invokes `ps` at
    # all -- proven here with a fake runner that raises TimeoutExpired
    # immediately, so the assertion is instant rather than waiting out a
    # real 5-second timeout.
    recorded_commands: list[list[str]] = []

    def _fake_runner(command, **kwargs):
        recorded_commands.append(list(command))
        raise subprocess.TimeoutExpired(cmd=command, timeout=5)

    result = GitRepoAdapter(tmp_path)._ls_remote_default_branch(runner=_fake_runner)

    assert result is None
    assert len(recorded_commands) == 1
    assert "ps" not in recorded_commands[0]
    assert "--ppid" not in recorded_commands[0]


def test_ls_remote_default_branch_returns_none_on_nonzero_exit(
    tmp_path: Path, repo: git.Repo
):
    # Old code relied on GitPython raising GitCommandError for a non-zero
    # exit; the hand-rolled subprocess call has to check returncode itself.
    def _fake_runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=128, stdout="", stderr="fatal: unreachable"
        )

    result = GitRepoAdapter(tmp_path)._ls_remote_default_branch(runner=_fake_runner)

    assert result is None


def test_ls_remote_default_branch_returns_none_when_git_missing(
    tmp_path: Path, repo: git.Repo
):
    def _fake_runner(command, **kwargs):
        raise FileNotFoundError("git executable not found")

    result = GitRepoAdapter(tmp_path)._ls_remote_default_branch(runner=_fake_runner)

    assert result is None


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


# ---------------------------------------------------------------------------
# build_patch (the "Create patch" feature): the whole point is a patch that
# `git apply` actually accepts, so most of these assert against real `git
# apply --check` output against a clean clone of the same repo, not just
# against the text this module happens to produce.
# ---------------------------------------------------------------------------


def _assert_patch_applies_cleanly(source_repo_path: Path, patch: str, clone_dir: Path) -> None:
    subprocess.run(
        ["git", "clone", str(source_repo_path), str(clone_dir)],
        check=True,
        capture_output=True,
    )
    patch_file = clone_dir / "check.patch"
    patch_file.write_text(patch, encoding="utf-8")
    result = subprocess.run(
        ["git", "apply", "--check", str(patch_file)],
        cwd=clone_dir,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"git apply --check rejected the generated patch: {result.stderr}"
    )


def test_build_patch_covers_modified_tracked_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("original CONTENT\n")

    patch = GitRepoAdapter(tmp_path).build_patch([Path("committed.txt")], [])

    assert "diff --git a/committed.txt b/committed.txt" in patch
    assert "-original content" in patch
    assert "+original CONTENT" in patch


def test_build_patch_covers_staged_change(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("staged content\n")
    repo.index.add(["committed.txt"])

    patch = GitRepoAdapter(tmp_path).build_patch([Path("committed.txt")], [])

    assert "diff --git a/committed.txt b/committed.txt" in patch
    assert "+staged content" in patch


def test_build_patch_covers_untracked_new_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "new_file.txt").write_text("brand new\n")

    patch = GitRepoAdapter(tmp_path).build_patch([], [Path("new_file.txt")])

    assert "diff --git a/new_file.txt b/new_file.txt" in patch
    assert "new file mode" in patch
    assert "--- /dev/null" in patch
    assert "+++ b/new_file.txt" in patch
    assert "+brand new" in patch


def test_build_patch_for_folder_covers_every_changed_file_under_it(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "tracked.txt").write_text("tracked one\n")
    repo.index.add(["sub/tracked.txt"])
    repo.index.commit("add sub/tracked.txt")

    (tmp_path / "sub" / "tracked.txt").write_text("tracked TWO\n")
    (tmp_path / "sub" / "untracked.txt").write_text("sub untracked\n")
    # Outside "sub" -- must not appear in a "sub"-scoped patch.
    (tmp_path / "committed.txt").write_text("outside change\n")

    patch = GitRepoAdapter(tmp_path).build_patch(
        [Path("sub/tracked.txt")], [Path("sub/untracked.txt")]
    )

    assert "diff --git a/sub/tracked.txt b/sub/tracked.txt" in patch
    assert "diff --git a/sub/untracked.txt b/sub/untracked.txt" in patch
    assert "committed.txt" not in patch


def test_build_patch_with_a_subset_of_tracked_paths_excludes_the_rest_and_applies_cleanly(
    tmp_path: Path, repo: git.Repo
):
    """The point of the file-selection dialog: two files changed under the
    same scope, but only one is named in `tracked_paths` -- the other must
    not merely be absent from the text, the resulting patch must still be
    something `git apply` accepts on its own (a patch naming only some of a
    commit's files is still perfectly valid, but a naive implementation
    could easily emit a header for one file with no hunk, or vice versa)."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "second.txt").write_text("second original\n")
    repo.index.add(["sub/second.txt"])
    repo.index.commit("add sub/second.txt")

    (tmp_path / "committed.txt").write_text("changed committed\n")
    (tmp_path / "sub" / "second.txt").write_text("changed second\n")

    patch = GitRepoAdapter(tmp_path).build_patch([Path("committed.txt")], [])

    assert "diff --git a/committed.txt b/committed.txt" in patch
    assert "second.txt" not in patch
    _assert_patch_applies_cleanly(tmp_path, patch, tmp_path.parent / "clean_checkout_subset")


def test_build_patch_expands_a_collapsed_untracked_directory_entry(
    tmp_path: Path, repo: git.Repo
):
    # An untracked directory is one collapsed FileChange (see
    # list_changes/tree_model), but git can only diff individual files
    # against /dev/null -- build_patch must walk it back out to real files.
    (tmp_path / "new_dir").mkdir()
    (tmp_path / "new_dir" / "a.txt").write_text("a content\n")
    (tmp_path / "new_dir" / "b.txt").write_text("b content\n")

    patch = GitRepoAdapter(tmp_path).build_patch([], [Path("new_dir")])

    assert "diff --git a/new_dir/a.txt b/new_dir/a.txt" in patch
    assert "diff --git a/new_dir/b.txt b/new_dir/b.txt" in patch


def test_build_patch_returns_empty_string_when_nothing_to_patch(
    tmp_path: Path, repo: git.Repo
):
    patch = GitRepoAdapter(tmp_path).build_patch([], [])

    assert patch == ""


def test_build_patch_skips_binary_untracked_file_without_raising(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "image.bin").write_bytes(bytes([0, 1, 2, 3, 0, 255]))

    patch = GitRepoAdapter(tmp_path).build_patch([], [Path("image.bin")])

    assert patch == ""


def test_build_patch_output_applies_cleanly_with_git_apply_check(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.txt").write_text("alpha\nbeta\n")
    repo.index.add(["sub/nested.txt"])
    repo.index.commit("add sub/nested.txt")

    (tmp_path / "committed.txt").write_text("original CONTENT\n")
    (tmp_path / "sub" / "nested.txt").write_text("alpha\nBETA\n")
    repo.index.add(["sub/nested.txt"])
    (tmp_path / "sub" / "new_file.txt").write_text("brand new\n")

    patch = GitRepoAdapter(tmp_path).build_patch(
        [Path("committed.txt"), Path("sub/nested.txt")], [Path("sub/new_file.txt")]
    )

    _assert_patch_applies_cleanly(tmp_path, patch, tmp_path.parent / "clean_checkout_whole_repo")


def test_build_patch_for_a_single_folder_applies_cleanly_with_git_apply_check(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.txt").write_text("alpha\nbeta\n")
    repo.index.add(["sub/nested.txt"])
    repo.index.commit("add sub/nested.txt")

    (tmp_path / "sub" / "nested.txt").write_text("alpha\nBETA\n")
    (tmp_path / "sub" / "new_file.txt").write_text("brand new\n")

    patch = GitRepoAdapter(tmp_path).build_patch(
        [Path("sub/nested.txt")], [Path("sub/new_file.txt")]
    )

    _assert_patch_applies_cleanly(tmp_path, patch, tmp_path.parent / "clean_checkout_folder")


# ---------------------------------------------------------------------------
# apply_patch: the "Apply patch..." feature's inverse of build_patch.
# ---------------------------------------------------------------------------


def test_apply_patch_restricted_to_one_selected_path_touches_only_that_file(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "second.txt").write_text("original second\n")
    repo.index.add(["second.txt"])
    repo.index.commit("add second.txt")

    (tmp_path / "committed.txt").write_text("changed committed\n")
    (tmp_path / "second.txt").write_text("changed second\n")

    patch = GitRepoAdapter(tmp_path).build_patch(
        [Path("committed.txt"), Path("second.txt")], []
    )

    # Reset the working tree back to HEAD -- build_patch above read the
    # working tree's current edits, apply_patch below re-applies them from
    # the patch text alone, restricted to a single file.
    repo.git.checkout("--", "committed.txt", "second.txt")
    assert (tmp_path / "committed.txt").read_text() == "original content\n"
    assert (tmp_path / "second.txt").read_text() == "original second\n"

    GitRepoAdapter(tmp_path).apply_patch(patch, [Path("committed.txt")])

    assert (tmp_path / "committed.txt").read_text() == "changed committed\n"
    # The unselected file must stay untouched even though it's in the same
    # patch text -- this is the whole point of --include.
    assert (tmp_path / "second.txt").read_text() == "original second\n"


def test_apply_patch_raises_and_leaves_the_working_tree_untouched_on_a_bad_patch(
    tmp_path: Path, repo: git.Repo
):
    original = (tmp_path / "committed.txt").read_text()

    with pytest.raises(git.GitCommandError):
        GitRepoAdapter(tmp_path).apply_patch(
            "not a valid patch at all\njust garbage text\n", [Path("committed.txt")]
        )

    assert (tmp_path / "committed.txt").read_text() == original
    # The dry-run --check must fail before any real apply runs, so nothing
    # ever lands as a staged/working-tree change either.
    assert GitRepoAdapter(tmp_path).list_changes() == []


def test_apply_patch_raises_when_the_check_dry_run_would_fail_to_apply_cleanly(
    tmp_path: Path, repo: git.Repo
):
    # A patch built against a *different* base content than what's currently
    # on disk (context lines won't match) -- --check must catch this and
    # refuse the real apply, rather than half-applying hunks that do match.
    (tmp_path / "committed.txt").write_text("some other content entirely\n")
    stale_patch = GitRepoAdapter(tmp_path).build_patch([Path("committed.txt")], [])
    repo.git.checkout("--", "committed.txt")
    (tmp_path / "committed.txt").write_text("a completely unrelated diverged version\n")

    with pytest.raises(git.GitCommandError):
        GitRepoAdapter(tmp_path).apply_patch(stale_patch, [Path("committed.txt")])

    assert (tmp_path / "committed.txt").read_text() == "a completely unrelated diverged version\n"


# ---------------------------------------------------------------------------
# list_stashes / stash_diff / apply_stash / pop_stash
# ---------------------------------------------------------------------------


def test_list_stashes_returns_empty_list_for_a_repo_with_no_stashes(
    tmp_path: Path, repo: git.Repo
):
    assert GitRepoAdapter(tmp_path).list_stashes() == []


def test_list_stashes_returns_newest_first_with_real_fields(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("first change\n")
    repo.git.stash("push", "-m", "first stash")
    (tmp_path / "committed.txt").write_text("second change\n")
    repo.git.stash("push", "-m", "second stash")

    entries = GitRepoAdapter(tmp_path).list_stashes()

    assert [e.message for e in entries] == [
        "On main: second stash",
        "On main: first stash",
    ]
    assert [e.ref for e in entries] == ["stash@{0}", "stash@{1}"]
    assert all(e.created_at is not None for e in entries)
    assert all(e.author == "Test User" for e in entries)


def test_list_stashes_message_with_colon_and_pipe_parses_intact(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("changed\n")
    repo.git.stash("push", "-m", "fix: broken thing | second half")

    entries = GitRepoAdapter(tmp_path).list_stashes()

    assert len(entries) == 1
    assert entries[0].message == "On main: fix: broken thing | second half"


def test_stash_diff_contains_the_changed_file_name(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("stashed change\n")
    repo.git.stash("push", "-m", "diff test")
    ref = GitRepoAdapter(tmp_path).list_stashes()[0].ref

    diff = GitRepoAdapter(tmp_path).stash_diff(ref)

    assert "committed.txt" in diff
    assert "stashed change" in diff


def test_apply_stash_restores_file_content_and_keeps_the_stash_entry(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "committed.txt").write_text("applied change\n")
    repo.git.stash("push", "-m", "apply test")
    ref = GitRepoAdapter(tmp_path).list_stashes()[0].ref

    GitRepoAdapter(tmp_path).apply_stash(ref)

    assert (tmp_path / "committed.txt").read_text() == "applied change\n"
    assert len(GitRepoAdapter(tmp_path).list_stashes()) == 1


def test_pop_stash_restores_file_content_and_removes_the_stash_entry(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "committed.txt").write_text("popped change\n")
    repo.git.stash("push", "-m", "pop test")
    ref = GitRepoAdapter(tmp_path).list_stashes()[0].ref

    GitRepoAdapter(tmp_path).pop_stash(ref)

    assert (tmp_path / "committed.txt").read_text() == "popped change\n"
    assert GitRepoAdapter(tmp_path).list_stashes() == []


def test_drop_stash_removes_the_entry_without_touching_the_working_tree(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "committed.txt").write_text("dropped change\n")
    repo.git.stash("push", "-m", "drop test")
    ref = GitRepoAdapter(tmp_path).list_stashes()[0].ref

    GitRepoAdapter(tmp_path).drop_stash(ref)

    assert GitRepoAdapter(tmp_path).list_stashes() == []
    # Unlike pop, drop never touches the working tree -- it stays at the
    # pre-stash (committed) content.
    assert (tmp_path / "committed.txt").read_text() == "original content\n"


def test_drop_stash_renumbers_remaining_stashes(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("first change\n")
    repo.git.stash("push", "-m", "first stash")
    (tmp_path / "committed.txt").write_text("second change\n")
    repo.git.stash("push", "-m", "second stash")
    (tmp_path / "committed.txt").write_text("third change\n")
    repo.git.stash("push", "-m", "third stash")
    adapter = GitRepoAdapter(tmp_path)
    # Drop the oldest (stash@{2}) -- the remaining two must renumber down.
    assert adapter.list_stashes()[2].message == "On main: first stash"

    adapter.drop_stash("stash@{2}")

    remaining = adapter.list_stashes()
    assert [e.message for e in remaining] == [
        "On main: third stash",
        "On main: second stash",
    ]
    assert [e.ref for e in remaining] == ["stash@{0}", "stash@{1}"]


def test_restore_file_from_stash_overwrites_only_that_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "other.txt").write_text("other content\n")
    repo.index.add(["other.txt"])
    repo.index.commit("add other.txt")
    (tmp_path / "committed.txt").write_text("stashed committed change\n")
    (tmp_path / "other.txt").write_text("stashed other change\n")
    repo.git.stash("push", "-m", "restore file test")
    ref = GitRepoAdapter(tmp_path).list_stashes()[0].ref
    # Simulate the working tree having moved on for the file we don't touch.
    (tmp_path / "other.txt").write_text("unrelated later edit\n")

    GitRepoAdapter(tmp_path).restore_file_from_stash(ref, Path("committed.txt"))

    assert (tmp_path / "committed.txt").read_text() == "stashed committed change\n"
    assert (tmp_path / "other.txt").read_text() == "unrelated later edit\n"
    # The stash entry itself is untouched by a file-level restore.
    assert len(GitRepoAdapter(tmp_path).list_stashes()) == 1


@pytest.mark.parametrize("bad_ref", ["; rm -rf /", "stash@{x}", "stash@0", "", "stash@{0}; ls"])
def test_stash_operations_reject_a_malformed_ref_without_invoking_git(
    tmp_path: Path, repo: git.Repo, bad_ref: str
):
    adapter = GitRepoAdapter(tmp_path)
    with pytest.raises(ValueError):
        adapter.stash_diff(bad_ref)
    with pytest.raises(ValueError):
        adapter.apply_stash(bad_ref)
    with pytest.raises(ValueError):
        adapter.pop_stash(bad_ref)
    with pytest.raises(ValueError):
        adapter.drop_stash(bad_ref)
    with pytest.raises(ValueError):
        adapter.restore_file_from_stash(bad_ref, Path("committed.txt"))


# --- File History: list_tracked_files ---------------------------------------


def test_list_tracked_files_returns_sorted_subtree_listing(tmp_path: Path, repo: git.Repo):
    (tmp_path / "zeta.txt").write_text("z\n")
    (tmp_path / "alpha.txt").write_text("a\n")
    repo.index.add(["zeta.txt", "alpha.txt"])
    repo.index.commit("add files")

    result = GitRepoAdapter(tmp_path).list_tracked_files(Path("."))

    assert [f.path for f in result.files] == [
        Path("alpha.txt"),
        Path("committed.txt"),
        Path("zeta.txt"),
    ]
    assert result.too_large is False


def test_list_tracked_files_excludes_binary_files_via_nul_sniff(tmp_path: Path, repo: git.Repo):
    (tmp_path / "binary.dat").write_bytes(b"\x00\x01binary")
    repo.index.add(["binary.dat"])
    repo.index.commit("add binary")

    result = GitRepoAdapter(tmp_path).list_tracked_files(Path("."))

    paths = [f.path for f in result.files]
    assert Path("binary.dat") not in paths
    assert Path("committed.txt") in paths


def test_list_tracked_files_flags_local_changes(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("edited but not committed\n")

    result = GitRepoAdapter(tmp_path).list_tracked_files(Path("."))

    match = next(f for f in result.files if f.path == Path("committed.txt"))
    assert match.has_local_changes is True


def test_list_tracked_files_too_large_past_the_cap(
    tmp_path: Path, repo: git.Repo, monkeypatch: pytest.MonkeyPatch
):
    # No per-file work at all past the cap -- monkeypatching it down to zero
    # is enough to prove the short-circuit fires without needing 5,000 files.
    monkeypatch.setattr(git_repo_adapter, "_FILE_HISTORY_SUBTREE_FILE_CAP", 0)

    result = GitRepoAdapter(tmp_path).list_tracked_files(Path("."))

    assert result.too_large is True
    assert result.files == []


def test_list_tracked_files_survives_submodule_gitlink_symlink_and_deleted_file(
    tmp_path: Path, repo: git.Repo, tmp_path_factory: pytest.TempPathFactory
):
    submodule_source = tmp_path_factory.mktemp("file_history_submodule_source")
    _init_repo_with_commit(submodule_source)
    # `git submodule add` refuses a local file:// clone by default on modern
    # git (CVE-2022-39253's fix) -- explicit opt-in is required here purely
    # because this test builds its own local submodule fixture, not because
    # the feature itself does anything with remotes.
    repo.git(c="protocol.file.allow=always").submodule("add", str(submodule_source), "sub")
    repo.git.commit("-m", "add submodule")

    (tmp_path / "linked.txt").symlink_to("committed.txt")
    repo.index.add(["linked.txt"])
    repo.index.commit("add symlink")

    (tmp_path / "gone.txt").write_text("will vanish\n")
    repo.index.add(["gone.txt"])
    repo.index.commit("add file that will be deleted from disk")
    (tmp_path / "gone.txt").unlink()

    result = GitRepoAdapter(tmp_path).list_tracked_files(Path("."))

    paths = [f.path for f in result.files]
    # Submodule gitlink: open() raises IsADirectoryError -- skipped, not listed.
    assert Path("sub") not in paths
    # Symlink: sniffed as text without reading through the link.
    assert Path("linked.txt") in paths
    # Tracked but deleted from disk: kept, treated as "not binary" rather
    # than as an error -- this is exactly what mode B's full-deletion branch
    # and the local-changes dot exist to surface.
    assert Path("gone.txt") in paths
    assert result.too_large is False


# --- File History: get_recent_commits (author) -------------------------------


def test_get_recent_commits_populates_author(tmp_path: Path, repo: git.Repo):
    commits = GitRepoAdapter(tmp_path).get_recent_commits(limit=1)

    assert commits[0].author == "Test User"


# --- File History: get_file_history -------------------------------------------


def test_get_file_history_returns_newest_first_honouring_limit(tmp_path: Path, repo: git.Repo):
    for i in range(3):
        (tmp_path / "committed.txt").write_text(f"content {i}\n")
        repo.index.add(["committed.txt"])
        repo.index.commit(f"commit {i}")

    result = GitRepoAdapter(tmp_path).get_file_history(Path("committed.txt"), limit=2)

    assert [e.commit.message for e in result.entries] == ["commit 2", "commit 1"]


def test_get_file_history_reports_rename_and_pre_rename_path(tmp_path: Path, repo: git.Repo):
    # This repo's own history contains zero renames -- build one by hand.
    repo.git.mv("committed.txt", "renamed.txt")
    repo.index.commit("rename committed to renamed")
    (tmp_path / "renamed.txt").write_text("edited after rename\n")
    repo.index.add(["renamed.txt"])
    repo.index.commit("edit after rename")

    result = GitRepoAdapter(tmp_path).get_file_history(Path("renamed.txt"), limit=10)

    assert result.entries[0].change_type == ChangeType.MODIFIED
    assert result.entries[0].path_at_commit == Path("renamed.txt")
    assert result.entries[0].renamed_from is None

    assert result.entries[1].change_type == ChangeType.RENAMED
    assert result.entries[1].path_at_commit == Path("renamed.txt")
    assert result.entries[1].renamed_from == Path("committed.txt")

    # The commit before the rename must carry the file's *old* path.
    assert result.entries[2].path_at_commit == Path("committed.txt")

    assert result.current_path == Path("renamed.txt")


def test_get_file_history_excludes_merge_commits(tmp_path: Path, repo: git.Repo):
    repo.git.checkout("-b", "feature")
    (tmp_path / "committed.txt").write_text("feature change\n")
    repo.index.add(["committed.txt"])
    repo.index.commit("feature commit")
    repo.git.checkout("main")
    (tmp_path / "other.txt").write_text("main change\n")
    repo.index.add(["other.txt"])
    repo.index.commit("main-only commit")
    repo.git.merge("feature", "--no-ff", "-m", "merge feature into main")

    result = GitRepoAdapter(tmp_path).get_file_history(Path("committed.txt"), limit=10)

    assert "merge feature into main" not in [e.commit.message for e in result.entries]
    assert "feature commit" in [e.commit.message for e in result.entries]


def test_get_file_history_current_path_none_when_file_deleted(tmp_path: Path, repo: git.Repo):
    repo.index.remove(["committed.txt"], working_tree=True)
    repo.index.commit("delete committed.txt")

    result = GitRepoAdapter(tmp_path).get_file_history(Path("committed.txt"), limit=10)

    assert result.entries[0].change_type == ChangeType.DELETED
    assert result.current_path is None


def test_get_file_history_zero_commit_repo_returns_empty_result_instead_of_raising(
    tmp_path: Path,
):
    # `git log` on a zero-commit repo exits 128 ("does not have any commits
    # yet") -- but list_tracked_files works fine before the first commit, so
    # a user can genuinely pick a staged-but-never-committed file here. That
    # must render as "no commits yet", never as a raised fatal error.
    git.Repo.init(tmp_path, initial_branch="main")
    (tmp_path / "staged.txt").write_text("x\n")

    result = GitRepoAdapter(tmp_path).get_file_history(Path("staged.txt"), limit=10)

    assert result.entries == []
    assert result.current_path == Path("staged.txt")


def test_get_file_history_parses_commit_with_control_bytes_in_message(
    tmp_path: Path, repo: git.Repo
):
    # A commit subject/body is untrusted free text and can legally contain
    # \x01/\x1f -- the NUL-separator guarantee means these must not misalign
    # the parse the way a \x01 or \x1f delimiter would have.
    (tmp_path / "committed.txt").write_text("more\n")
    repo.index.add(["committed.txt"])
    repo.git.commit("-m", "subject\x01with\x1fcontrol bytes")

    result = GitRepoAdapter(tmp_path).get_file_history(Path("committed.txt"), limit=10)

    assert result.entries[0].commit.message == "subject\x01with\x1fcontrol bytes"


# --- File History: get_commit_file_diff (unchanged -- regression guard) ------


def test_get_commit_file_diff_unchanged_for_renamed_file(tmp_path: Path, repo: git.Repo):
    repo.git.mv("committed.txt", "renamed.txt")
    commit = repo.index.commit("rename committed to renamed")

    diff = GitRepoAdapter(tmp_path).get_commit_file_diff(
        commit.hexsha, Path("renamed.txt"), Path("committed.txt")
    )

    removed = [
        line.text for hunk in diff.hunks for line in hunk.lines if line.kind == DiffLineKind.REMOVED
    ]
    assert diff.new_ref == commit.hexsha[:8]
    assert removed == []  # a pure rename with no content change has no removed lines


# --- File History: get_file_diff_against_disk (mode B) -----------------------


def test_get_file_diff_against_disk_shows_drift_against_current_disk_content(
    tmp_path: Path, repo: git.Repo
):
    commit = repo.head.commit
    (tmp_path / "committed.txt").write_text("changed on disk\n")

    diff = GitRepoAdapter(tmp_path).get_file_diff_against_disk(
        commit.hexsha, Path("committed.txt"), Path("committed.txt")
    )

    removed = [
        line.text for hunk in diff.hunks for line in hunk.lines if line.kind == DiffLineKind.REMOVED
    ]
    added = [
        line.text for hunk in diff.hunks for line in hunk.lines if line.kind == DiffLineKind.ADDED
    ]
    assert "original content" in removed
    assert "changed on disk" in added
    assert diff.new_ref == "working tree"


def test_get_file_diff_against_disk_across_a_rename(tmp_path: Path, repo: git.Repo):
    old_commit = repo.head.commit
    repo.git.mv("committed.txt", "renamed.txt")
    repo.index.commit("rename committed to renamed")
    (tmp_path / "renamed.txt").write_text("edited after rename\n")

    # Exact object addressing (cat-file <sha>:<path_at_commit>) is what makes
    # this correct across the rename: path_at_commit is the file's *old*
    # name, current_path is its new one.
    diff = GitRepoAdapter(tmp_path).get_file_diff_against_disk(
        old_commit.hexsha, Path("committed.txt"), Path("renamed.txt")
    )

    removed = [
        line.text for hunk in diff.hunks for line in hunk.lines if line.kind == DiffLineKind.REMOVED
    ]
    added = [
        line.text for hunk in diff.hunks for line in hunk.lines if line.kind == DiffLineKind.ADDED
    ]
    assert "original content" in removed
    assert "edited after rename" in added


def test_get_file_diff_against_disk_renders_full_deletion_when_path_gone(
    tmp_path: Path, repo: git.Repo
):
    commit = repo.head.commit

    diff = GitRepoAdapter(tmp_path).get_file_diff_against_disk(
        commit.hexsha, Path("committed.txt"), None
    )

    removed = [
        line.text for hunk in diff.hunks for line in hunk.lines if line.kind == DiffLineKind.REMOVED
    ]
    assert "original content" in removed
    assert diff.new_ref == "(deleted)"


def test_get_file_diff_against_disk_on_unmodified_file_returns_empty_diff_not_error(
    tmp_path: Path, repo: git.Repo
):
    # --no-index exits 0 (not 1) when the two sides are identical -- this is
    # the *ordinary* result of opening mode B on the newest commit with no
    # local edits, not an edge case, and it must render as an empty diff
    # rather than as an error.
    commit = repo.head.commit

    diff = GitRepoAdapter(tmp_path).get_file_diff_against_disk(
        commit.hexsha, Path("committed.txt"), Path("committed.txt")
    )

    assert diff.hunks == []


def test_get_file_diff_against_disk_reports_binary_as_placeholder_not_empty_hunks(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "binary.dat").write_bytes(b"\x00\x01binary")
    repo.index.add(["binary.dat"])
    commit = repo.index.commit("add binary")

    diff = GitRepoAdapter(tmp_path).get_file_diff_against_disk(
        commit.hexsha, Path("binary.dat"), Path("binary.dat")
    )

    assert len(diff.hunks) == 1
    assert "Binary file" in diff.hunks[0].lines[0].text


def test_get_file_diff_against_disk_raises_on_git_failure(tmp_path: Path, repo: git.Repo):
    with pytest.raises(git.GitCommandError):
        GitRepoAdapter(tmp_path).get_file_diff_against_disk(
            "0" * 40, Path("committed.txt"), Path("committed.txt")
        )
