from dataclasses import dataclass

from local_changes_viewer.core.domain.diff import DiffLine, DiffLineKind, DiffResult


@dataclass
class PairedLine:
    left_text: str | None
    left_kind: DiffLineKind | None
    right_text: str | None
    right_kind: DiffLineKind | None
    left_lineno: int | None = None
    right_lineno: int | None = None


def pair_hunk_lines(lines: list[DiffLine]) -> list[PairedLine]:
    paired: list[PairedLine] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.kind is DiffLineKind.CONTEXT:
            paired.append(
                PairedLine(line.text, None, line.text, None, line.old_lineno, line.new_lineno)
            )
            i += 1
            continue

        removed: list[DiffLine] = []
        while i < len(lines) and lines[i].kind is DiffLineKind.REMOVED:
            removed.append(lines[i])
            i += 1
        added: list[DiffLine] = []
        while i < len(lines) and lines[i].kind is DiffLineKind.ADDED:
            added.append(lines[i])
            i += 1

        for row in range(max(len(removed), len(added))):
            left = removed[row] if row < len(removed) else None
            right = added[row] if row < len(added) else None
            paired.append(
                PairedLine(
                    left.text if left is not None else None,
                    DiffLineKind.REMOVED if left is not None else None,
                    right.text if right is not None else None,
                    DiffLineKind.ADDED if right is not None else None,
                    left.old_lineno if left is not None else None,
                    right.new_lineno if right is not None else None,
                )
            )
    return paired


def pair_substitution_indices(lines: list[DiffLine]) -> list[tuple[int, int]]:
    """Returns (removed_index, added_index) pairs, as indices into `lines`,
    for same-row-position REMOVED/ADDED lines within a run - i.e. lines that
    represent a like-for-like substitution suitable for intraline diffing."""
    pairs: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if lines[i].kind is DiffLineKind.CONTEXT:
            i += 1
            continue

        removed_idxs: list[int] = []
        while i < len(lines) and lines[i].kind is DiffLineKind.REMOVED:
            removed_idxs.append(i)
            i += 1
        added_idxs: list[int] = []
        while i < len(lines) and lines[i].kind is DiffLineKind.ADDED:
            added_idxs.append(i)
            i += 1

        for row in range(min(len(removed_idxs), len(added_idxs))):
            pairs.append((removed_idxs[row], added_idxs[row]))
    return pairs


def reconstruct_old_file_lines(diff: DiffResult) -> list[str]:
    """Rebuilds the pre-change (old) side of the file as a full line list,
    for showing the left pane's whole original source in edit mode.

    Every CONTEXT and REMOVED line already carries its own `old_lineno`, so
    each one is placed at `old_lineno - 1` in the result rather than simply
    appended in encounter order. Placement-by-number is what keeps this
    correct even if git ever omitted some context between hunks (possible
    for a file bigger than the `--unified=100000` window `compute_diff`
    asks for) -- naive appending would silently shift every line number
    below such a gap, while indexing by `old_lineno` cannot drift.

    Returns `[]` when the diff has no CONTEXT/REMOVED lines at all -- an
    untracked/new file has no "old" side to reconstruct.
    """
    max_lineno = 0
    for hunk in diff.hunks:
        for line in hunk.lines:
            if line.kind is not DiffLineKind.ADDED and line.old_lineno is not None:
                max_lineno = max(max_lineno, line.old_lineno)
    if max_lineno == 0:
        return []

    result = [""] * max_lineno
    for hunk in diff.hunks:
        for line in hunk.lines:
            if line.kind is not DiffLineKind.ADDED and line.old_lineno is not None:
                result[line.old_lineno - 1] = line.text
    return result
