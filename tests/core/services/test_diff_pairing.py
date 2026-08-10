from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.core.services.diff_pairing import (
    pair_hunk_lines,
    pair_substitution_indices,
    reconstruct_old_file_lines,
)


def test_pairs_context_line_on_both_sides() -> None:
    lines = [DiffLine(DiffLineKind.CONTEXT, 1, 1, "same")]

    paired = pair_hunk_lines(lines)

    assert len(paired) == 1
    assert paired[0].left_text == "same"
    assert paired[0].right_text == "same"
    assert paired[0].left_kind is None
    assert paired[0].right_kind is None


def test_pairs_equal_length_removed_and_added_runs_row_by_row() -> None:
    lines = [
        DiffLine(DiffLineKind.REMOVED, 1, None, "old1"),
        DiffLine(DiffLineKind.REMOVED, 2, None, "old2"),
        DiffLine(DiffLineKind.ADDED, None, 1, "new1"),
        DiffLine(DiffLineKind.ADDED, None, 2, "new2"),
    ]

    paired = pair_hunk_lines(lines)

    assert len(paired) == 2
    assert paired[0].left_text == "old1"
    assert paired[0].right_text == "new1"
    assert paired[1].left_text == "old2"
    assert paired[1].right_text == "new2"


def test_pairs_unequal_length_runs_leaving_unmatched_side_none() -> None:
    lines = [
        DiffLine(DiffLineKind.REMOVED, 1, None, "old1"),
        DiffLine(DiffLineKind.ADDED, None, 1, "new1"),
        DiffLine(DiffLineKind.ADDED, None, 2, "new2"),
    ]

    paired = pair_hunk_lines(lines)

    assert len(paired) == 2
    assert paired[0].left_text == "old1"
    assert paired[0].right_text == "new1"
    assert paired[1].left_text is None
    assert paired[1].left_kind is None
    assert paired[1].right_text == "new2"
    assert paired[1].right_kind is DiffLineKind.ADDED


def test_pair_substitution_indices_matches_same_row_removed_added() -> None:
    lines = [
        DiffLine(DiffLineKind.CONTEXT, 1, 1, "same"),
        DiffLine(DiffLineKind.REMOVED, 2, None, "old1"),
        DiffLine(DiffLineKind.REMOVED, 3, None, "old2"),
        DiffLine(DiffLineKind.ADDED, None, 2, "new1"),
    ]

    pairs = pair_substitution_indices(lines)

    assert pairs == [(1, 3)]


def test_reconstruct_old_file_lines_from_contiguous_hunk() -> None:
    hunk = DiffHunk(
        old_start=1,
        old_count=3,
        new_start=1,
        new_count=2,
        lines=[
            DiffLine(DiffLineKind.CONTEXT, 1, 1, "one"),
            DiffLine(DiffLineKind.REMOVED, 2, None, "two"),
            DiffLine(DiffLineKind.CONTEXT, 3, 2, "three"),
        ],
    )
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[hunk])

    assert reconstruct_old_file_lines(diff) == ["one", "two", "three"]


def test_reconstruct_old_file_lines_places_by_lineno_across_a_gap() -> None:
    """Two hunks with a gap between them (as if git's context window had
    cut off lines 4-8) -- placing by `old_lineno` rather than append order
    must leave the gap as blanks instead of shifting line 9 up to sit
    right after line 3."""
    first_hunk = DiffHunk(
        old_start=1,
        old_count=2,
        new_start=1,
        new_count=2,
        lines=[
            DiffLine(DiffLineKind.CONTEXT, 1, 1, "one"),
            DiffLine(DiffLineKind.REMOVED, 2, None, "two"),
            DiffLine(DiffLineKind.CONTEXT, 3, 1, "three"),
        ],
    )
    second_hunk = DiffHunk(
        old_start=9,
        old_count=1,
        new_start=2,
        new_count=1,
        lines=[
            DiffLine(DiffLineKind.CONTEXT, 9, 2, "nine"),
        ],
    )
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[first_hunk, second_hunk])

    result = reconstruct_old_file_lines(diff)

    assert result == ["one", "two", "three", "", "", "", "", "", "nine"]


def test_reconstruct_old_file_lines_returns_empty_for_new_file_with_no_old_side() -> None:
    hunk = DiffHunk(
        old_start=0,
        old_count=0,
        new_start=1,
        new_count=2,
        lines=[
            DiffLine(DiffLineKind.ADDED, None, 1, "one"),
            DiffLine(DiffLineKind.ADDED, None, 2, "two"),
        ],
    )
    diff = DiffResult(old_ref="(none)", new_ref="working tree", hunks=[hunk])

    assert reconstruct_old_file_lines(diff) == []

