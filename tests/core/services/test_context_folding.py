from local_changes_viewer.core.domain.diff import DiffLine, DiffLineKind
from local_changes_viewer.core.services.context_folding import FoldedRun, VisibleRun, fold_context


def _context(n: int, start: int = 1) -> list[DiffLine]:
    return [
        DiffLine(DiffLineKind.CONTEXT, i, i, f"line{i}") for i in range(start, start + n)
    ]


def test_short_context_run_stays_visible() -> None:
    lines = _context(2) + [DiffLine(DiffLineKind.ADDED, None, 3, "new")]

    segments = fold_context(lines)

    assert segments == [VisibleRun(lines[:2]), VisibleRun(lines[2:])]


def test_long_context_run_between_changes_folds_middle_keeping_margins() -> None:
    before = [DiffLine(DiffLineKind.REMOVED, 1, None, "old")]
    middle = _context(20, start=2)
    after = [DiffLine(DiffLineKind.ADDED, None, 22, "new")]
    lines = before + middle + after

    segments = fold_context(lines)

    assert segments[0] == VisibleRun(before)
    assert segments[1] == VisibleRun(middle[:3])
    assert segments[2] == FoldedRun(middle[3:-3])
    assert segments[3] == VisibleRun(middle[-3:])
    assert segments[4] == VisibleRun(after)


def test_long_context_run_at_file_start_has_no_head_margin() -> None:
    leading = _context(20)
    change = [DiffLine(DiffLineKind.ADDED, None, 21, "new")]
    lines = leading + change

    segments = fold_context(lines)

    assert segments[0] == FoldedRun(leading[:-3])
    assert segments[1] == VisibleRun(leading[-3:])
    assert segments[2] == VisibleRun(change)


def test_long_context_run_at_file_end_has_no_tail_margin() -> None:
    change = [DiffLine(DiffLineKind.REMOVED, 1, None, "old")]
    trailing = _context(20, start=2)
    lines = change + trailing

    segments = fold_context(lines)

    assert segments[0] == VisibleRun(change)
    assert segments[1] == VisibleRun(trailing[:3])
    assert segments[2] == FoldedRun(trailing[3:])
