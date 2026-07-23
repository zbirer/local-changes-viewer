from dataclasses import dataclass

from local_changes_viewer.core.domain.diff import DiffLine, DiffLineKind

FOLD_THRESHOLD = 3
CONTEXT_MARGIN = 3


@dataclass
class VisibleRun:
    lines: list[DiffLine]


@dataclass
class FoldedRun:
    lines: list[DiffLine]


Segment = VisibleRun | FoldedRun


def fold_context(lines: list[DiffLine]) -> list[Segment]:
    """Splits a hunk's lines into segments, collapsing long unchanged
    (CONTEXT) runs into FoldedRun segments while keeping CONTEXT_MARGIN lines
    visible on either side of a change for readability."""
    segments: list[Segment] = []
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].kind is not DiffLineKind.CONTEXT:
            j = i
            while j < n and lines[j].kind is not DiffLineKind.CONTEXT:
                j += 1
            segments.append(VisibleRun(lines[i:j]))
            i = j
            continue

        j = i
        while j < n and lines[j].kind is DiffLineKind.CONTEXT:
            j += 1
        run = lines[i:j]

        head_margin = 0 if i == 0 else CONTEXT_MARGIN
        tail_margin = 0 if j == n else CONTEXT_MARGIN
        hidden_count = len(run) - head_margin - tail_margin

        if hidden_count < FOLD_THRESHOLD:
            segments.append(VisibleRun(run))
        else:
            if head_margin:
                segments.append(VisibleRun(run[:head_margin]))
            segments.append(FoldedRun(run[head_margin : len(run) - tail_margin]))
            if tail_margin:
                segments.append(VisibleRun(run[len(run) - tail_margin :]))
        i = j
    return segments
