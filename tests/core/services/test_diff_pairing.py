from local_changes_viewer.core.domain.diff import DiffLine, DiffLineKind
from local_changes_viewer.core.services.diff_pairing import (
    pair_hunk_lines,
    pair_substitution_indices,
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

