from dataclasses import dataclass

from local_changes_viewer.core.domain.diff import DiffLine, DiffLineKind, DiffResult


@dataclass
class ChangeRun:
    """One maximal contiguous run of REMOVED and/or ADDED lines -- what a
    user thinks of as "a change", as opposed to a git hunk (which, under
    `compute_diff`'s `--unified=100000`, is usually the *entire file* and so
    is useless as a navigation unit -- see `change_runs` below)."""

    old_lineno: int
    new_lineno: int


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


def change_runs(diff: DiffResult) -> list[ChangeRun]:
    """Returns one `ChangeRun` per maximal contiguous group of REMOVED
    and/or ADDED lines, in file order across ALL of the diff's hunks -- the
    unit "Prev change"/"Next change" navigation jumps between.

    This exists because `compute_diff` asks git for `--unified=100000`,
    which collapses a normal-size file's diff into a single `@@` hunk no
    matter how many separate edits it contains -- so counting/jumping by
    git hunk (the old behavior) only ever finds one target, the top of the
    file. A "change run" is what the folded diff view already treats as
    one visual unit (the `⋯ N unchanged lines ⋯` markers in
    `context_folding.fold_context` only ever fold *unchanged* runs, so a
    change run can never itself end up hidden inside one).

    A pure insertion (no REMOVED line in the run) has no old-side line of
    its own to report, so `old_lineno` falls back to one past the closest
    preceding CONTEXT line's `old_lineno` seen so far -- `1` if the
    insertion sits before any context, i.e. at the very top of the file.
    `new_lineno` falls back symmetrically for a pure deletion. Runs are
    also flushed at each hunk boundary, since two hunks that both start or
    end without a shared context line may be separated by content git
    simply didn't include in either hunk -- merging them would silently
    drop a navigation stop.

    The "closest preceding CONTEXT line" trackers are reset at every hunk
    boundary too, re-seeded from that hunk's own declared `old_start`/
    `new_start` rather than left holding the previous hunk's last CONTEXT
    line. Without this, a run that is the very first thing in a hunk (no
    CONTEXT line of its own yet to update the tracker) would fall back to
    a position borrowed from wherever the PREVIOUS hunk happened to end --
    an unrelated part of the file. Per the unified-diff header convention
    (see `git_repo_adapter.py`'s hunk-header parsing), `old_start` already
    equals the real line number of the hunk's first old-side line when
    `old_count > 0`, so seeding one below it (`old_start - 1`) reproduces
    the "one past preceding context" fallback exactly; when `old_count`
    is 0 (the whole hunk has no old-side line at all, e.g. a pure
    insertion), `old_start` itself already denotes the last real old line
    *before* the hunk per that same convention, so it is used unshifted.
    `new_start`/`new_count` are seeded symmetrically.

    Returns `[]` when the diff has no REMOVED/ADDED lines at all.
    """
    runs: list[ChangeRun] = []
    pending: list[DiffLine] = []
    last_context_old = 0
    last_context_new = 0

    def flush() -> None:
        if not pending:
            return
        removed = [line for line in pending if line.kind is DiffLineKind.REMOVED]
        added = [line for line in pending if line.kind is DiffLineKind.ADDED]
        if removed and removed[0].old_lineno is not None:
            old_lineno = removed[0].old_lineno
        else:
            old_lineno = last_context_old + 1
        if added and added[0].new_lineno is not None:
            new_lineno = added[0].new_lineno
        else:
            new_lineno = last_context_new + 1
        runs.append(ChangeRun(old_lineno=old_lineno, new_lineno=new_lineno))
        pending.clear()

    for hunk in diff.hunks:
        flush()
        last_context_old = hunk.old_start - 1 if hunk.old_count > 0 else hunk.old_start
        last_context_new = hunk.new_start - 1 if hunk.new_count > 0 else hunk.new_start
        for line in hunk.lines:
            if line.kind is DiffLineKind.CONTEXT:
                flush()
                if line.old_lineno is not None:
                    last_context_old = line.old_lineno
                if line.new_lineno is not None:
                    last_context_new = line.new_lineno
            else:
                pending.append(line)
    flush()
    return runs
