from dataclasses import dataclass

from local_changes_viewer.core.domain.diff import DiffLine, DiffLineKind


@dataclass
class PairedLine:
    left_text: str | None
    left_kind: DiffLineKind | None
    right_text: str | None
    right_kind: DiffLineKind | None


def pair_hunk_lines(lines: list[DiffLine]) -> list[PairedLine]:
    paired: list[PairedLine] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.kind is DiffLineKind.CONTEXT:
            paired.append(PairedLine(line.text, None, line.text, None))
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
