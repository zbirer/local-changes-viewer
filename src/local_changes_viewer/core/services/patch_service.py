"""Builds the "Create Patch" feature's git-apply-able patch text, and parses
+ applies patch text for the inverse "Apply patch..." feature.

Thin wrapper over `GitRepoAdapter.build_patch`/`apply_patch`, mirroring
`diff_service.py`'s adapter-factory shape. The things this layer owns that the
adapter can't: (1) deciding which files under a target are candidates in the
first place -- `files_in_scope()`, which is what the file-selection dialog
offers checkboxes for -- (2) splitting the user's final selection into
tracked vs. untracked before handing it to the adapter, since telling those
apart needs `Repository.changes` (the workspace's already-scanned state)
rather than a fresh disk/`git status` scan the adapter has no reason to
repeat -- and (3) parsing arbitrary patch text (from a file or the clipboard,
for "Apply patch...") into the same `FileChange` list shape the file-
selection dialog already knows how to render, so both features share one
dialog.
"""

import re
from collections.abc import Callable, Collection
from pathlib import Path

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange, PatchFileDiff
from local_changes_viewer.core.domain.repository import Repository
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter

# Matches a `diff --git a/<old> b/<new>` header's quoted form -- git wraps
# each side in double quotes (C-style backslash escapes) only when a path
# needs it (non-ASCII bytes, embedded quotes/backslashes/control chars);
# the vast majority of patches never hit this branch.
_QUOTED_DIFF_GIT_RE = re.compile(r'^"a/(?P<old>(?:[^"\\]|\\.)*)" "b/(?P<new>(?:[^"\\]|\\.)*)"$')

# Unquoted form: "a/<old> b/<new>". Splitting an unquoted "a/X b/Y" line is
# inherently ambiguous when X or Y itself contains " b/" -- this picks the
# first " b/" after the "a/" prefix, which is right for every ordinary path
# and simply a best-effort guess for the pathological case, matching this
# parser's general "skip/best-effort rather than crash" stance on malformed
# input.
_UNQUOTED_DIFF_GIT_RE = re.compile(r"^a/(?P<old>.*?) b/(?P<new>.*)$")


class PatchService:
    def __init__(
        self,
        adapter_factory: Callable[[Path], GitRepoAdapter] | None = None,
    ) -> None:
        self._adapter_factory = adapter_factory or GitRepoAdapter

    def files_in_scope(self, repo: Repository, target_relpath: Path) -> list[FileChange]:
        """Returns every change under `target_relpath` (repo-relative;
        `Path(".")` means the whole repo) that the "Create patch" dialog
        should offer a checkbox for -- tracked or untracked, but never an
        ignored path, since those were never part of what this feature
        patches in the first place.

        `Path.is_relative_to` also returns True when the two paths are equal,
        so this one check covers a single file selected directly, a
        subfolder, and the repo-root case (`Path(".").is_relative_to(...)`
        matches everything) without special-casing any of them.

        Sorted by path so the dialog lists the same target the same way
        every time -- callers (the dialog) display this order as-is rather
        than re-sorting it themselves.
        """
        in_scope = [
            change
            for change in repo.changes
            if change.change_type != ChangeType.IGNORED
            and change.path.is_relative_to(target_relpath)
        ]
        return sorted(in_scope, key=lambda change: change.path.as_posix())

    def build_patch(self, repo: Repository, selected_paths: Collection[Path]) -> str:
        """Builds a patch covering only `selected_paths` -- the subset of
        `files_in_scope()`'s offering the user left checked in the dialog.

        Splits the selection into tracked vs. untracked here (not in the
        adapter) because that distinction comes from `Repository.changes`,
        the same source `files_in_scope()` reads -- the adapter itself has
        no notion of "in scope," only "diff this path" / "diff that path
        against /dev/null".
        """
        selected = set(selected_paths)
        tracked_paths = [
            change.path
            for change in repo.changes
            if change.change_type not in (ChangeType.UNTRACKED, ChangeType.IGNORED)
            and change.path in selected
        ]
        untracked_paths = [
            change.path
            for change in repo.changes
            if change.change_type == ChangeType.UNTRACKED and change.path in selected
        ]
        adapter = self._adapter_factory(repo.path)
        return adapter.build_patch(tracked_paths, untracked_paths)

    def parse_patch(self, patch_text: str) -> list[FileChange]:
        """Parses raw patch text (from a file or the clipboard, for "Apply
        patch...") into the list of files it touches, in the same shape
        `files_in_scope()` returns -- so `PatchFileSelectionDialog` can be
        reused unchanged for both "Create patch" and "Apply patch...".

        Implemented in terms of `split_patch()` -- the one place that walks
        `diff --git` boundaries -- so this and the stash-diff file list can
        never drift apart on what counts as a file or a change type.
        """
        return [
            FileChange(path=diff.path, change_type=diff.change_type)
            for diff in self.split_patch(patch_text)
        ]

    def split_patch(self, patch_text: str) -> list[PatchFileDiff]:
        """Splits raw multi-file patch text into one `PatchFileDiff` per
        file, each carrying the complete per-file chunk verbatim (starting
        at its `diff --git` line, ending just before the next one) -- the
        exact slice `GitRepoAdapter.parse_unified_diff` expects, so a caller
        (e.g. `StashesDialog`) can render one selected file's diff without
        re-splitting the whole patch itself.

        A malformed/unparseable `diff --git` header drops its whole chunk
        rather than raising -- same "one bad header shouldn't hide every
        other file" stance `parse_patch` has always had; the user's patch
        text may be hand-edited or truncated (this is exactly what the
        clipboard edit box exists for).
        """
        lines = patch_text.splitlines()
        total = len(lines)
        chunk_starts = [i for i, line in enumerate(lines) if line.startswith("diff --git ")]

        diffs: dict[Path, PatchFileDiff] = {}
        for position, start in enumerate(chunk_starts):
            end = chunk_starts[position + 1] if position + 1 < len(chunk_starts) else total
            chunk = lines[start:end]
            paths = self._parse_diff_git_header_paths(chunk[0].removeprefix("diff --git "))
            if paths is None:
                # Header didn't parse -- drop this whole chunk; the next
                # iteration resumes at the next "diff --git " line.
                continue
            old_path, new_path = paths

            change_type = ChangeType.MODIFIED
            is_delete = False
            for extended_header in chunk[1:]:
                if extended_header.startswith("new file mode"):
                    change_type = ChangeType.ADDED
                elif extended_header.startswith("deleted file mode"):
                    change_type = ChangeType.DELETED
                    is_delete = True
                elif extended_header.startswith("rename from") or extended_header.startswith(
                    "rename to"
                ):
                    change_type = ChangeType.MODIFIED

            chosen_path = Path(old_path if is_delete else new_path)
            diffs[chosen_path] = PatchFileDiff(
                path=chosen_path,
                change_type=change_type,
                diff_text="\n".join(chunk),
            )

        return sorted(diffs.values(), key=lambda diff: diff.path.as_posix())

    @staticmethod
    def _parse_diff_git_header_paths(rest: str) -> tuple[str, str] | None:
        """`rest` is a `diff --git ` line with that literal prefix already
        stripped. Returns the (old, new) path strings, or None if the line
        doesn't match either the quoted or unquoted header shape git emits.
        """
        quoted = _QUOTED_DIFF_GIT_RE.match(rest)
        if quoted:
            try:
                old = quoted.group("old").encode("utf-8").decode("unicode_escape")
                new = quoted.group("new").encode("utf-8").decode("unicode_escape")
            except (UnicodeDecodeError, UnicodeEncodeError):
                return None
            return old, new

        unquoted = _UNQUOTED_DIFF_GIT_RE.match(rest)
        if unquoted:
            return unquoted.group("old"), unquoted.group("new")
        return None

    def apply_patch(
        self, repo: Repository, patch_text: str, selected_paths: Collection[Path]
    ) -> None:
        """Applies `patch_text` to `repo`'s working tree, restricted to
        `selected_paths` -- the subset of `parse_patch()`'s findings the user
        left checked in `PatchFileSelectionDialog`. All the actual git work
        (dry-run check, then real apply) lives in the adapter; this just
        resolves which adapter instance to run it against, mirroring
        `build_patch()` above.
        """
        adapter = self._adapter_factory(repo.path)
        adapter.apply_patch(patch_text, selected_paths)
