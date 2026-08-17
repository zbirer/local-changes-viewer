from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.core.services.diff_pairing import (
    change_runs,
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


def test_change_runs_finds_multiple_separate_runs_inside_a_single_hunk() -> None:
    """Reproduces the `--unified=100000` reality `compute_diff` actually
    produces: three well-separated edits that git still reports as ONE
    hunk. Counting/jumping by git hunk would find only one target; this is
    the case that requires counting by change run instead."""
    hunk = DiffHunk(
        old_start=1,
        old_count=9,
        new_start=1,
        new_count=9,
        lines=[
            DiffLine(DiffLineKind.REMOVED, 1, None, "old1"),
            DiffLine(DiffLineKind.ADDED, None, 1, "new1"),
            DiffLine(DiffLineKind.CONTEXT, 2, 2, "same-a"),
            DiffLine(DiffLineKind.CONTEXT, 3, 3, "same-b"),
            DiffLine(DiffLineKind.CONTEXT, 4, 4, "same-c"),
            DiffLine(DiffLineKind.REMOVED, 5, None, "old2"),
            DiffLine(DiffLineKind.ADDED, None, 5, "new2"),
            DiffLine(DiffLineKind.CONTEXT, 6, 6, "same-d"),
            DiffLine(DiffLineKind.CONTEXT, 7, 7, "same-e"),
            DiffLine(DiffLineKind.CONTEXT, 8, 8, "same-f"),
            DiffLine(DiffLineKind.REMOVED, 9, None, "old3"),
            DiffLine(DiffLineKind.ADDED, None, 9, "new3"),
        ],
    )
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[hunk])

    runs = change_runs(diff)

    assert [(r.old_lineno, r.new_lineno) for r in runs] == [(1, 1), (5, 5), (9, 9)]


def test_change_runs_pure_insertion_uses_position_after_preceding_context() -> None:
    hunk = DiffHunk(
        old_start=1,
        old_count=2,
        new_start=1,
        new_count=3,
        lines=[
            DiffLine(DiffLineKind.CONTEXT, 1, 1, "one"),
            DiffLine(DiffLineKind.ADDED, None, 2, "inserted"),
            DiffLine(DiffLineKind.CONTEXT, 2, 3, "two"),
        ],
    )
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[hunk])

    runs = change_runs(diff)

    assert len(runs) == 1
    assert runs[0].old_lineno == 2  # 1 + preceding context's old_lineno (1)
    assert runs[0].new_lineno == 2


def test_change_runs_pure_insertion_at_top_of_file_uses_old_lineno_one() -> None:
    hunk = DiffHunk(
        old_start=0,
        old_count=0,
        new_start=1,
        new_count=1,
        lines=[
            DiffLine(DiffLineKind.ADDED, None, 1, "inserted"),
        ],
    )
    diff = DiffResult(old_ref="(none)", new_ref="working tree", hunks=[hunk])

    runs = change_runs(diff)

    assert len(runs) == 1
    assert runs[0].old_lineno == 1
    assert runs[0].new_lineno == 1


def test_change_runs_pure_deletion_uses_position_after_preceding_context() -> None:
    hunk = DiffHunk(
        old_start=1,
        old_count=3,
        new_start=1,
        new_count=2,
        lines=[
            DiffLine(DiffLineKind.CONTEXT, 1, 1, "one"),
            DiffLine(DiffLineKind.REMOVED, 2, None, "deleted"),
            DiffLine(DiffLineKind.CONTEXT, 3, 2, "two"),
        ],
    )
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[hunk])

    runs = change_runs(diff)

    assert len(runs) == 1
    assert runs[0].old_lineno == 2
    assert runs[0].new_lineno == 2  # 1 + preceding context's new_lineno (1)


def test_change_runs_returns_empty_list_for_diff_with_no_changes() -> None:
    hunk = DiffHunk(
        old_start=1,
        old_count=1,
        new_start=1,
        new_count=1,
        lines=[DiffLine(DiffLineKind.CONTEXT, 1, 1, "same")],
    )
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[hunk])

    assert change_runs(diff) == []


def test_change_runs_returns_empty_list_for_diff_with_no_hunks() -> None:
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[])

    assert change_runs(diff) == []


def test_change_runs_pure_addition_opening_a_later_hunk_uses_that_hunks_own_start() -> None:
    """Reproduces defect: `last_context_old` must not survive a hunk
    boundary. hunk1 ends with its last CONTEXT line at old_lineno=3;
    hunk2 opens with an ADDED line before any CONTEXT line of its own, so
    without a per-hunk reseed the stale old_lineno=3 leaks in and produces
    a wrong fallback of 4 -- a position back in hunk1's territory instead
    of hunk2's own old_start=50."""
    hunk1 = DiffHunk(
        old_start=1,
        old_count=3,
        new_start=1,
        new_count=3,
        lines=[
            DiffLine(DiffLineKind.CONTEXT, 1, 1, "one"),
            DiffLine(DiffLineKind.CONTEXT, 2, 2, "two"),
            DiffLine(DiffLineKind.CONTEXT, 3, 3, "three"),
        ],
    )
    hunk2 = DiffHunk(
        old_start=50,
        old_count=1,
        new_start=48,
        new_count=2,
        lines=[
            DiffLine(DiffLineKind.ADDED, None, 48, "inserted"),
            DiffLine(DiffLineKind.CONTEXT, 50, 49, "fifty"),
        ],
    )
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[hunk1, hunk2])

    runs = change_runs(diff)

    assert len(runs) == 1
    assert runs[0].old_lineno == 50, "must use hunk2's own old_start, not hunk1's leftover context"
    assert runs[0].new_lineno == 48


def test_change_runs_pure_deletion_opening_a_later_hunk_uses_that_hunks_own_start() -> None:
    """Symmetric case for the new-side tracker: hunk2 opens with a REMOVED
    line before any CONTEXT line of its own."""
    hunk1 = DiffHunk(
        old_start=1,
        old_count=3,
        new_start=1,
        new_count=3,
        lines=[
            DiffLine(DiffLineKind.CONTEXT, 1, 1, "one"),
            DiffLine(DiffLineKind.CONTEXT, 2, 2, "two"),
            DiffLine(DiffLineKind.CONTEXT, 3, 3, "three"),
        ],
    )
    hunk2 = DiffHunk(
        old_start=48,
        old_count=2,
        new_start=50,
        new_count=1,
        lines=[
            DiffLine(DiffLineKind.REMOVED, 48, None, "deleted"),
            DiffLine(DiffLineKind.CONTEXT, 49, 50, "fifty"),
        ],
    )
    diff = DiffResult(old_ref="HEAD", new_ref="working tree", hunks=[hunk1, hunk2])

    runs = change_runs(diff)

    assert len(runs) == 1
    assert runs[0].old_lineno == 48
    assert runs[0].new_lineno == 50, "must use hunk2's own new_start, not hunk1's leftover context"

