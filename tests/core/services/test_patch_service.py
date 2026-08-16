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


# ---------------------------------------------------------------------------
# files_in_scope: what the "Create patch" file-selection dialog offers
# checkboxes for.
# ---------------------------------------------------------------------------


def test_files_in_scope_for_whole_repo_includes_every_change_sorted_by_path(
    tmp_path: Path, repo: git.Repo
):
    repository = _make_repository(
        tmp_path,
        changes=[
            FileChange(path=Path("z.txt"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("a.txt"), change_type=ChangeType.UNTRACKED),
        ],
    )

    changes = PatchService().files_in_scope(repository, Path("."))

    assert [c.path for c in changes] == [Path("a.txt"), Path("z.txt")]


def test_files_in_scope_excludes_changes_outside_the_target_folder(
    tmp_path: Path, repo: git.Repo
):
    repository = _make_repository(
        tmp_path,
        changes=[
            FileChange(path=Path("sub/extra.txt"), change_type=ChangeType.UNTRACKED),
            FileChange(path=Path("outside.txt"), change_type=ChangeType.UNTRACKED),
        ],
    )

    changes = PatchService().files_in_scope(repository, Path("sub"))

    assert [c.path for c in changes] == [Path("sub/extra.txt")]


def test_files_in_scope_excludes_changes_outside_a_single_file_target(
    tmp_path: Path, repo: git.Repo
):
    repository = _make_repository(
        tmp_path,
        changes=[
            FileChange(path=Path("sub/nested.txt"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("sub/sibling.txt"), change_type=ChangeType.UNTRACKED),
        ],
    )

    changes = PatchService().files_in_scope(repository, Path("sub/nested.txt"))

    assert [c.path for c in changes] == [Path("sub/nested.txt")]


def test_files_in_scope_excludes_ignored_paths(tmp_path: Path, repo: git.Repo):
    repository = _make_repository(
        tmp_path,
        changes=[
            FileChange(path=Path("committed.txt"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("build"), change_type=ChangeType.IGNORED, is_directory=True),
        ],
    )

    changes = PatchService().files_in_scope(repository, Path("."))

    assert [c.path for c in changes] == [Path("committed.txt")]


def test_files_in_scope_empty_for_a_clean_repo(tmp_path: Path, repo: git.Repo):
    repository = _make_repository(tmp_path, changes=[])

    assert PatchService().files_in_scope(repository, Path(".")) == []


# ---------------------------------------------------------------------------
# build_patch: builds the patch for exactly the caller-selected subset --
# the file-selection dialog's job is deciding that subset, this method's job
# is honoring it without re-deriving scope from a target path.
# ---------------------------------------------------------------------------


def test_build_patch_includes_only_the_selected_paths(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("changed\n")
    (tmp_path / "sub" / "extra.txt").write_text("extra\n")
    repository = _make_repository(
        tmp_path,
        changes=[
            FileChange(path=Path("committed.txt"), change_type=ChangeType.MODIFIED),
            FileChange(path=Path("sub/extra.txt"), change_type=ChangeType.UNTRACKED),
        ],
    )

    patch = PatchService().build_patch(repository, [Path("sub/extra.txt")])

    assert "sub/extra.txt" in patch
    assert "committed.txt" not in patch


def test_build_patch_can_include_both_a_selected_tracked_and_untracked_file(
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

    patch = PatchService().build_patch(
        repository, [Path("committed.txt"), Path("sub/extra.txt")]
    )

    assert "diff --git a/committed.txt b/committed.txt" in patch
    assert "diff --git a/sub/extra.txt b/sub/extra.txt" in patch


def test_build_patch_returns_empty_string_when_selection_is_empty(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "committed.txt").write_text("changed\n")
    repository = _make_repository(
        tmp_path,
        changes=[FileChange(path=Path("committed.txt"), change_type=ChangeType.MODIFIED)],
    )

    patch = PatchService().build_patch(repository, [])

    assert patch == ""


def test_build_patch_returns_empty_string_for_a_clean_repo(tmp_path: Path, repo: git.Repo):
    repository = _make_repository(tmp_path, changes=[])

    patch = PatchService().build_patch(repository, [Path(".")])

    assert patch == ""


# ---------------------------------------------------------------------------
# parse_patch: the "Apply patch..." feature's inverse of build_patch --
# turns real `git diff`-shaped patch text back into the FileChange list
# PatchFileSelectionDialog already knows how to render.
# ---------------------------------------------------------------------------


def test_parse_patch_modified_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("changed content\n")
    patch = repo.git.diff("--no-color", "HEAD", "--", "committed.txt")

    changes = PatchService().parse_patch(patch)

    assert changes == [FileChange(path=Path("committed.txt"), change_type=ChangeType.MODIFIED)]


def test_parse_patch_new_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "brand_new.txt").write_text("new content\n")
    repo.index.add(["brand_new.txt"])
    patch = repo.git.diff("--no-color", "HEAD", "--", "brand_new.txt")

    changes = PatchService().parse_patch(patch)

    assert changes == [FileChange(path=Path("brand_new.txt"), change_type=ChangeType.ADDED)]


def test_parse_patch_deleted_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").unlink()
    patch = repo.git.diff("--no-color", "HEAD", "--", "committed.txt")

    changes = PatchService().parse_patch(patch)

    assert changes == [FileChange(path=Path("committed.txt"), change_type=ChangeType.DELETED)]


def test_parse_patch_renamed_file(tmp_path: Path, repo: git.Repo):
    repo.git.mv("committed.txt", "renamed.txt")
    patch = repo.git.diff("--no-color", "-M", "HEAD")

    changes = PatchService().parse_patch(patch)

    # Rename headers collapse to a single MODIFIED entry on the new path --
    # not ChangeType.RENAMED -- per the "Apply patch..." brief.
    assert changes == [FileChange(path=Path("renamed.txt"), change_type=ChangeType.MODIFIED)]


def test_parse_patch_multiple_files_sorted_and_deduplicated(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("changed\n")
    (tmp_path / "sub" / "nested.txt").write_text("changed nested\n")
    (tmp_path / "sub" / "another_new.txt").write_text("new\n")
    repo.index.add(["sub/another_new.txt"])
    patch = repo.git.diff("--no-color", "HEAD")

    changes = PatchService().parse_patch(patch)

    assert [c.path for c in changes] == [
        Path("committed.txt"),
        Path("sub/another_new.txt"),
        Path("sub/nested.txt"),
    ]
    by_path = {c.path: c.change_type for c in changes}
    assert by_path[Path("committed.txt")] == ChangeType.MODIFIED
    assert by_path[Path("sub/nested.txt")] == ChangeType.MODIFIED
    assert by_path[Path("sub/another_new.txt")] == ChangeType.ADDED


def test_parse_patch_malformed_header_is_skipped_not_raised(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("changed\n")
    good_patch = repo.git.diff("--no-color", "HEAD", "--", "committed.txt")
    malformed = "diff --git this is not a valid header\nsome junk\n" + good_patch

    changes = PatchService().parse_patch(malformed)

    assert changes == [FileChange(path=Path("committed.txt"), change_type=ChangeType.MODIFIED)]


def test_parse_patch_empty_input_returns_empty_list(tmp_path: Path, repo: git.Repo):
    assert PatchService().parse_patch("") == []


def test_parse_patch_garbage_input_returns_empty_list(tmp_path: Path, repo: git.Repo):
    assert PatchService().parse_patch("not a patch at all\njust some text\n") == []


# ---------------------------------------------------------------------------
# split_patch: the one place that walks `diff --git` boundaries -- parse_patch
# is implemented in terms of this, so every test below also stands as an
# anti-drift guard for parse_patch's paths/change-types on the same input.
# ---------------------------------------------------------------------------


def test_split_patch_single_file_returns_one_chunk_starting_at_its_diff_git_line(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "committed.txt").write_text("changed content\n")
    patch = repo.git.diff("--no-color", "HEAD", "--", "committed.txt")

    diffs = PatchService().split_patch(patch)

    assert len(diffs) == 1
    diff = diffs[0]
    assert diff.path == Path("committed.txt")
    assert diff.change_type == ChangeType.MODIFIED
    assert diff.diff_text.startswith("diff --git a/committed.txt b/committed.txt")
    assert diff.diff_text.strip() == patch.strip()


def test_split_patch_multiple_files_each_chunk_contains_only_its_own_file(
    tmp_path: Path, repo: git.Repo
):
    (tmp_path / "committed.txt").write_text("changed\n")
    (tmp_path / "sub" / "nested.txt").write_text("changed nested\n")
    patch = repo.git.diff("--no-color", "HEAD")

    diffs = PatchService().split_patch(patch)

    assert [d.path for d in diffs] == [Path("committed.txt"), Path("sub/nested.txt")]
    committed_diff, nested_diff = diffs
    assert committed_diff.diff_text.startswith("diff --git a/committed.txt b/committed.txt")
    assert "nested.txt" not in committed_diff.diff_text
    assert nested_diff.diff_text.startswith("diff --git a/sub/nested.txt b/sub/nested.txt")
    assert "committed.txt" not in nested_diff.diff_text


def test_split_patch_added_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "brand_new.txt").write_text("new content\n")
    repo.index.add(["brand_new.txt"])
    patch = repo.git.diff("--no-color", "HEAD", "--", "brand_new.txt")

    diffs = PatchService().split_patch(patch)

    assert len(diffs) == 1
    assert diffs[0].path == Path("brand_new.txt")
    assert diffs[0].change_type == ChangeType.ADDED
    assert diffs[0].diff_text.startswith("diff --git a/brand_new.txt b/brand_new.txt")


def test_split_patch_deleted_file(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").unlink()
    patch = repo.git.diff("--no-color", "HEAD", "--", "committed.txt")

    diffs = PatchService().split_patch(patch)

    assert len(diffs) == 1
    assert diffs[0].path == Path("committed.txt")
    assert diffs[0].change_type == ChangeType.DELETED
    assert diffs[0].diff_text.startswith("diff --git a/committed.txt b/committed.txt")


def test_split_patch_renamed_file(tmp_path: Path, repo: git.Repo):
    repo.git.mv("committed.txt", "renamed.txt")
    patch = repo.git.diff("--no-color", "-M", "HEAD")

    diffs = PatchService().split_patch(patch)

    # Same "rename collapses to MODIFIED on the new path" stance parse_patch
    # has always had.
    assert len(diffs) == 1
    assert diffs[0].path == Path("renamed.txt")
    assert diffs[0].change_type == ChangeType.MODIFIED
    assert diffs[0].diff_text.startswith("diff --git a/committed.txt b/renamed.txt")


def test_split_patch_malformed_header_drops_only_that_chunk(tmp_path: Path, repo: git.Repo):
    (tmp_path / "committed.txt").write_text("changed\n")
    good_patch = repo.git.diff("--no-color", "HEAD", "--", "committed.txt")
    malformed = "diff --git this is not a valid header\nsome junk\n" + good_patch

    diffs = PatchService().split_patch(malformed)

    assert len(diffs) == 1
    assert diffs[0].path == Path("committed.txt")
    assert "some junk" not in diffs[0].diff_text


def test_split_patch_empty_input_returns_empty_list(tmp_path: Path, repo: git.Repo):
    assert PatchService().split_patch("") == []


def test_split_patch_and_parse_patch_agree_on_paths_and_change_types(
    tmp_path: Path, repo: git.Repo
):
    # Anti-drift guard: split_patch and parse_patch must never disagree on
    # what counts as a file or a change type for the same input -- parse_patch
    # is implemented in terms of split_patch specifically to make that
    # impossible, this just proves it on a realistic multi-file input.
    (tmp_path / "committed.txt").write_text("changed\n")
    (tmp_path / "sub" / "nested.txt").write_text("changed nested\n")
    (tmp_path / "sub" / "another_new.txt").write_text("new\n")
    repo.index.add(["sub/another_new.txt"])
    patch = repo.git.diff("--no-color", "HEAD")

    split_result = PatchService().split_patch(patch)
    parse_result = PatchService().parse_patch(patch)

    assert [FileChange(path=d.path, change_type=d.change_type) for d in split_result] == (
        parse_result
    )


# ---------------------------------------------------------------------------
# apply_patch: delegates to the adapter for the repo the file belongs to,
# mirroring build_patch's adapter-factory shape.
# ---------------------------------------------------------------------------


def test_apply_patch_delegates_to_the_adapter_for_the_repo_path(tmp_path: Path, repo: git.Repo):
    calls: list = []

    class _FakeAdapter:
        def __init__(self, path: Path) -> None:
            calls.append(("factory", path))

        def apply_patch(self, patch_text: str, selected_paths) -> None:
            calls.append(("apply_patch", patch_text, list(selected_paths)))

    repository = _make_repository(tmp_path, changes=[])
    service = PatchService(adapter_factory=_FakeAdapter)

    service.apply_patch(repository, "some patch text", [Path("a.txt")])

    assert calls == [
        ("factory", tmp_path),
        ("apply_patch", "some patch text", [Path("a.txt")]),
    ]
