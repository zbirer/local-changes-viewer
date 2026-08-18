import os
import re
import subprocess
import tempfile
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import Callable

import git

from local_changes_viewer.core.domain.commit_log_entry import CommitLogEntry
from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.file_history import (
    FileHistoryCommit,
    FileHistoryResult,
    TrackedFile,
    TrackedFilesResult,
)
from local_changes_viewer.core.domain.repository import BranchStatus
from local_changes_viewer.core.domain.stash_entry import StashEntry
from local_changes_viewer.core.domain.worktree_info import WorktreeInfo
from local_changes_viewer.core.infra.cancel_token import CancelToken
from local_changes_viewer.core.services import workspace_cache

_BRANCH_LINE_RE = re.compile(
    r"^## (?P<branch>\S+?)(?:\.\.\.(?P<upstream>\S+))?(?: \[(?P<info>[^\]]+)\])?$"
)
# `git status --porcelain=v1 --branch` prints this instead of the usual
# "## <branch>...<upstream>" line on a brand-new repo with zero commits
# (verified against real git output: "## No commits yet on main"). It
# doesn't match _BRANCH_LINE_RE, so without this it fell through to using
# the whole "No commits yet on main" string as the branch name.
_NO_COMMITS_LINE_RE = re.compile(r"^## No commits yet on (?P<branch>\S+)$")
_AHEAD_BEHIND_RE = re.compile(r"(ahead|behind) (\d+)")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_INDEX_LINE_RE = re.compile(r"^index (\w+)\.\.(\w+)")
# `stash list`/`stash show`/`stash apply`/`stash pop` all take a ref of this
# exact shape ("stash@{0}", "stash@{12}", ...) -- every ref this adapter ever
# passes to git comes straight out of parsing `git stash list`'s own output
# (see list_stashes), so this is a belt-and-suspenders check, not a real
# parser: it exists purely so a caller passing an arbitrary/malformed string
# (e.g. from a corrupted parse, or a caller that skipped list_stashes
# entirely) can never turn into an arbitrary git argument.
_STASH_REF_RE = re.compile(r"^stash@\{\d+\}$")

_STATUS_CODE_TO_CHANGE_TYPE = {
    "??": ChangeType.UNTRACKED,
    "!!": ChangeType.IGNORED,
}


_NOT_COMPUTED = object()  # sentinel: distinguishes "never computed" from a real None result

# How long a cached default-branch answer is trusted before re-asking the
# remote. A repo's default branch is a deliberate, infrequent repo-level
# decision (a rename/re-point on GitHub, say), not something that drifts
# gradually — so a full day is long enough that almost every scan in a
# workday is a cache hit (no network call at all), while still bounding how
# long a rename can go unnoticed to "worst case, one day," which is fine for
# a UI annotation rather than something correctness-critical downstream.
_DEFAULT_BRANCH_CACHE_TTL_SECONDS = 24 * 60 * 60

# How long _ls_remote_default_branch waits for the remote before giving up
# (see the comment on that method for why this is a hand-rolled subprocess
# timeout rather than GitPython's kill_after_timeout).
_LS_REMOTE_TIMEOUT_SECONDS = 5

# Matches the `runner: Runner = subprocess.run` injection seam used by
# worktree_terminal_service.py, so tests can substitute a fake without
# actually shelling out or waiting out a real timeout.
LsRemoteRunner = Callable[..., subprocess.CompletedProcess]

# How many file names an untracked-directory diff placeholder lists before
# falling back to "... and N more" — keeps a `node_modules`-sized directory's
# summary short enough to actually read.
_UNTRACKED_DIR_SUMMARY_MAX_NAMES = 20

# Hard cap on how many files _summarize_untracked_directory will even walk
# before giving up and reporting "N+" — without this, a directory with tens
# of thousands of entries would make every double-click on it walk the whole
# tree just to print a count nobody needs exactly.
_UNTRACKED_DIR_SUMMARY_SCAN_LIMIT = 5000

# File History's folder-scoped file search: above this many tracked files
# under the searched subtree, list_tracked_files gives up on a per-file
# listing entirely (no sniffing, no local-changes lookup) and reports
# `too_large=True` instead -- the search box's own live-filtering promise
# ("type 2 characters") is meaningless against a subtree this size anyway.
_FILE_HISTORY_SUBTREE_FILE_CAP = 5000

# How many bytes of each tracked file list_tracked_files sniffs for a NUL
# byte before deciding "binary, exclude it" -- a bound on the same idiom
# already used at `_diff_untracked` (see the `b"\x00" in raw_bytes` check
# below), just capped rather than reading the whole file: this runs once per
# file in the subtree, not once for a single clicked file.
_FILE_HISTORY_SNIFF_BYTES = 8192

# `git log --follow`'s sentinel-delimited format for File History's commit
# list (get_file_history). NUL, not \x01/\x1f/\x02: a commit subject or body
# is untrusted free text and can legally contain those other control bytes,
# which would silently misalign every field after them -- NUL is the one
# byte git itself refuses to let into a commit message ("a NUL byte in
# commit log message not allowed"), so it's the only separator guaranteed
# never to appear inside a field it's supposed to be delimiting.
_FILE_HISTORY_LOG_FORMAT = "%x00%H%x00%an%x00%cI%x00%s%x00%B%x00"

# Hard cap on how many bytes _diff_untracked will read into memory before
# giving up and reporting "too large" instead of the real content — the
# untracked-directory case just above already bounds its own walk for the
# same reason (a `node_modules`-sized entry shouldn't block the UI thread);
# without this, double-clicking one huge untracked file (a data dump, a
# vendored binary) reads the whole thing onto that same thread synchronously.
_UNTRACKED_FILE_DIFF_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB


class GitRepoAdapter:
    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path
        self._repo = git.Repo(repo_path)
        # _find_default_branch's network fallback is a real round trip, so its
        # result is memoized for the adapter's lifetime — a scan otherwise
        # pays for it once per call site even though it can't change mid-scan.
        self._default_branch_cache: object = _NOT_COMPUTED

    def list_changes(self, include_unpushed_commits: bool = False) -> list[FileChange]:
        # `-z` NUL-terminates every field instead of using a space/newline
        # and the literal " -> " separator the default text format relies on
        # for renames -- so a filename containing " -> ", a newline, or a
        # tab can never corrupt the split. `-c core.quotePath=false` backs
        # this up: without it (and without `-z`, which git also treats as
        # disabling quoting on its own), a non-ASCII filename comes back
        # C-quoted into an escaped literal like "\327\251...", which the
        # rest of the app -- most importantly the later `git diff --
        # <path>` -- would never match against the real file on disk.
        output = self._repo.git(c="core.quotePath=false").status(
            "--porcelain=v1", "-z", "--ignored"
        )
        changes: list[FileChange] = []
        tokens = output.split("\0")
        i = 0
        while i < len(tokens):
            entry = tokens[i]
            if not entry:
                i += 1
                continue
            xy = entry[:2]
            rest = entry[3:]
            old_path: Path | None = None
            if "R" in xy:
                # `-z` reverses the rename field order versus the default
                # text format ("from -> to" becomes "to", NUL, "from") -- the
                # old path is the *next* NUL-terminated token, never embedded
                # in this one via " -> ".
                old_path = Path(tokens[i + 1])
                i += 2
            else:
                i += 1
            is_directory = rest.endswith("/")
            if not is_directory and xy in _STATUS_CODE_TO_CHANGE_TYPE:
                # An untracked/ignored path with no trailing slash is normally
                # a plain file — but git status never descends into a
                # symlink even when it points at a directory (e.g. a
                # symlinked node_modules), so it's reported the same way a
                # file would be. Stat only here (never for tracked M/A/D/R
                # entries, which are always files) to catch that case, so a
                # folder-filter rule like `equals:'node_modules'` still
                # matches it the same as a real directory.
                is_directory = (self._repo_path / Path(rest)).is_dir()
            changes.append(
                FileChange(
                    path=Path(rest),
                    change_type=self._classify(xy),
                    old_path=old_path,
                    is_directory=is_directory,
                )
            )

        if include_unpushed_commits:
            existing_paths = {c.path for c in changes}
            changes.extend(
                change
                for change in self._list_unpushed_commit_changes()
                if change.path not in existing_paths
            )

        return changes

    def _list_unpushed_commit_changes(self) -> list[FileChange]:
        base = self._get_unpushed_diff_base()
        if base is None:
            return []
        try:
            # See `_parse_name_status_z` for why `-z` (+ quotePath=false).
            output = self._repo.git(c="core.quotePath=false").diff(
                "--no-color", "--name-status", "-M", "-z", f"{base}...HEAD"
            )
        except git.GitCommandError:
            return []

        changes: list[FileChange] = []
        for code, path, old_path in self._parse_name_status_z(output):
            change_type = (
                ChangeType.RENAMED
                if code.startswith("R")
                else {"A": ChangeType.ADDED, "D": ChangeType.DELETED}.get(
                    code[0], ChangeType.MODIFIED
                )
            )
            changes.append(
                FileChange(
                    path=path,
                    change_type=change_type,
                    old_path=old_path,
                    is_unpushed_commit=True,
                    commit_message=self._get_commit_messages(base, path),
                )
            )
        return changes

    @staticmethod
    def _parse_name_status_z(output: str) -> list[tuple[str, Path, Path | None]]:
        """Parses `--name-status -z` output into (code, path, old_path) triples.

        `-z` NUL-terminates every field instead of the default tab/newline
        format, so a filename containing a tab or newline can't corrupt the
        split -- and (paired with `-c core.quotePath=false` at the call
        site) a non-ASCII filename comes back as its real UTF-8 bytes
        instead of git's default C-quoted escape (e.g. "\\327\\251..."),
        which would never match the real path on disk in a later `git diff
        -- <path>`. Unlike `git status -z` (see `list_changes`), `--name-
        status -z` does NOT reverse rename field order -- a rename/copy
        entry is simply three consecutive tokens (code, old path, new path)
        instead of the usual two.
        """
        entries: list[tuple[str, Path, Path | None]] = []
        tokens = output.split("\0")
        i = 0
        while i < len(tokens):
            code = tokens[i]
            if not code:
                i += 1
                continue
            if code.startswith("R") or code.startswith("C"):
                old_path, path = Path(tokens[i + 1]), Path(tokens[i + 2])
                i += 3
            else:
                path = Path(tokens[i + 1])
                old_path = None
                i += 2
            entries.append((code, path, old_path))
        return entries

    def _get_commit_messages(self, base: str, path: Path) -> str | None:
        try:
            output = self._repo.git.log("--format=%s", f"{base}..HEAD", "--", str(path))
        except git.GitCommandError:
            return None
        return output.strip() or None

    def _get_unpushed_diff_base(self) -> str | None:
        # The ref to diff HEAD against when reporting "unpushed" commits/files.
        # A configured upstream is authoritative. Without one (e.g. a local
        # feature branch that was never pushed), there is nothing to diff
        # against by definition -- but has_unpushed_changes() still reports
        # such a branch as unpushed (any commit here "has nowhere it could
        # have been pushed to"), so file-level listing/diffing falls back to
        # the repo's default branch to stay consistent with that verdict,
        # rather than silently showing no files for a branch flagged "Yes".
        upstream = self._get_upstream_ref()
        if upstream is not None:
            return upstream
        default_branch = self._find_default_branch()
        if default_branch is None:
            return None
        try:
            current_branch = self._repo.active_branch.name
        except TypeError:
            current_branch = None
        if default_branch == current_branch:
            # Without a remote, the "default branch" heuristic can fall back
            # to git's global init.defaultBranch config, which may just name
            # whatever branch is already checked out here -- diffing a
            # branch against itself always yields zero, which would wrongly
            # say "nothing unpushed" for a branch that in fact has nowhere
            # to be pushed to at all.
            return None
        for candidate in (f"origin/{default_branch}", default_branch):
            try:
                self._repo.git.rev_parse("--verify", "--quiet", candidate)
            except git.GitCommandError:
                continue
            return candidate
        return None

    def _get_upstream_ref(self) -> str | None:
        try:
            return self._repo.git.rev_parse("--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        except git.GitCommandError:
            return None

    def get_branch_status(self) -> BranchStatus:
        output = self._repo.git.status("--porcelain=v1", "--branch")
        first_line = output.splitlines()[0]

        if first_line == "## HEAD (no branch)":
            return BranchStatus(branch_name="HEAD", ahead=0, behind=0)

        no_commits_match = _NO_COMMITS_LINE_RE.match(first_line)
        if no_commits_match:
            return BranchStatus(branch_name=no_commits_match.group("branch"), ahead=0, behind=0)

        match = _BRANCH_LINE_RE.match(first_line)
        if not match:
            return BranchStatus(branch_name=first_line.removeprefix("## "), ahead=0, behind=0)

        ahead = 0
        behind = 0
        info = match.group("info")
        if info:
            for kind, count in _AHEAD_BEHIND_RE.findall(info):
                if kind == "ahead":
                    ahead = int(count)
                else:
                    behind = int(count)

        branch_name = match.group("branch")
        return BranchStatus(
            branch_name=branch_name,
            ahead=ahead,
            behind=behind,
            parent_branch=self._find_local_parent_branch(branch_name),
            default_branch=self._find_default_branch(),
        )

    def get_recent_commits(self, limit: int = 5) -> list[CommitLogEntry]:
        commits = list(self._repo.iter_commits(max_count=limit))
        return [
            CommitLogEntry(
                hexsha=commit.hexsha,
                short_hexsha=commit.hexsha[:8],
                message=commit.message.strip().splitlines()[0] if commit.message.strip() else "",
                committed_datetime=commit.committed_datetime,
                branch_name=self._get_branch_for_commit(commit.hexsha),
                full_message=commit.message.strip(),
                author=commit.author.name,
            )
            for commit in commits
        ]

    def _get_branch_for_commit(self, hexsha: str) -> str:
        try:
            output = self._repo.git.branch("--contains", hexsha, "--format=%(refname:short)")
        except git.GitCommandError:
            return ""
        # `git branch --contains` also lists a synthetic pseudo-entry for
        # the *current* detached-HEAD state itself (literally
        # "(HEAD detached at <sha>)", verified against real git output) --
        # not a real branch name, and its leading "(" sorts before every
        # real branch name alphabetically, so it must never win either the
        # alphabetical fallback or (accidentally) a preference match below.
        names = [
            line.strip()
            for line in output.splitlines()
            if line.strip() and not line.strip().startswith("(")
        ]
        if not names:
            return ""
        # `git branch --contains` prints matches in plain alphabetical
        # order, so a commit reachable from both "feature-x" and "main"
        # always reported "feature-x" regardless of which branch the user
        # is actually looking at. Least-surprising fix, in priority order:
        # (1) the repo's current branch, since the commit log is almost
        # always being viewed *from* it; (2) the repo's default branch, the
        # next most likely "this is what the commit really belongs to"
        # answer; (3) only then fall back to git's own alphabetical order.
        try:
            current_branch = self._repo.active_branch.name
        except TypeError:
            current_branch = None
        if current_branch in names:
            return current_branch
        default_branch = self._find_default_branch()
        if default_branch in names:
            return default_branch
        return names[0]

    def get_commit_files(self, commit_hexsha: str) -> list[FileChange]:
        # See `_parse_name_status_z` for why `-z` (+ quotePath=false).
        output = self._repo.git(c="core.quotePath=false").show(
            "--no-color", "--name-status", "--pretty=format:", "-M", "-z", commit_hexsha
        )
        changes: list[FileChange] = []
        for code, path, old_path in self._parse_name_status_z(output):
            change_type = (
                ChangeType.RENAMED
                if code.startswith("R")
                else {"A": ChangeType.ADDED, "D": ChangeType.DELETED}.get(
                    code[0], ChangeType.MODIFIED
                )
            )
            changes.append(FileChange(path=path, change_type=change_type, old_path=old_path))
        return changes

    def get_commit_file_diff(
        self, commit_hexsha: str, file_path: Path, old_path: Path | None = None
    ) -> DiffResult:
        args = ["--no-color", "-M", "--unified=100000", commit_hexsha, "--"]
        if old_path:
            args.append(str(old_path))
        args.append(str(file_path))
        raw = self._repo.git.show(*args)
        return self.parse_unified_diff(
            raw, old_ref=f"{commit_hexsha[:8]}~1", new_ref=commit_hexsha[:8]
        )

    def list_tracked_files(self, subtree: Path) -> TrackedFilesResult:
        """Lists tracked, non-binary files under `subtree` (repo-relative;
        `Path(".")` scans the whole repo), each flagged with whether it has
        uncommitted local changes -- the source list File History's folder
        search filters and ranks.
        """
        output = self._repo.git(c="core.quotePath=false").ls_files("-z", "--", str(subtree))
        paths = [Path(token) for token in output.split("\0") if token]
        if len(paths) > _FILE_HISTORY_SUBTREE_FILE_CAP:
            # No per-file work at all past the cap -- not even the
            # local-changes lookup below -- since the result is thrown away
            # regardless of what it would have said.
            return TrackedFilesResult(too_large=True)

        # Reuses list_changes()'s existing `--porcelain=v1 -z --ignored`
        # parser rather than hand-rolling a second one: it already solves
        # the `-z` rename-token reversal and non-ASCII quoting this would
        # otherwise have to re-solve from scratch.
        changed_paths = {
            change.path
            for change in self.list_changes()
            if change.change_type != ChangeType.IGNORED and change.path.is_relative_to(subtree)
        }

        files: list[TrackedFile] = []
        for path in sorted(paths, key=str):
            if self._is_binary_tracked_file(path):
                continue
            files.append(TrackedFile(path=path, has_local_changes=path in changed_paths))
        return TrackedFilesResult(files=files)

    def _is_binary_tracked_file(self, path: Path) -> bool:
        """True if `path` should be excluded from a File History listing as
        binary -- but a `git ls-files` entry isn't always a plain readable
        file, and an uncaught exception here would fail the *whole* subtree
        listing over one bad entry rather than just skipping it:

        - a submodule gitlink is a directory on disk, so `open()` raises
          `IsADirectoryError` -- caller treats that the same as binary
          (excluded), since a submodule has no file history of its own here.
        - a tracked-but-deleted-from-disk file (not yet staged) makes
          `open()` raise `FileNotFoundError` -- this is kept, not excluded,
          because it's exactly what mode B's full-deletion branch and the
          local-changes dot exist to surface, so it's treated as "not
          binary" rather than as an error.
        - a symlink's `open()` follows the link and sniffs the *target*
          (wrong -- git stores the link text itself as the blob) and raises
          on a dangling link -- sniffed as text without reading through.
        """
        if os.path.islink(self._repo_path / path):
            return False
        try:
            with open(self._repo_path / path, "rb") as file:
                chunk = file.read(_FILE_HISTORY_SNIFF_BYTES)
        except IsADirectoryError:
            return True
        except FileNotFoundError:
            return False
        except OSError:
            # Any other unreadable entry (permissions, etc.) -- never let
            # one bad path take down the whole listing.
            return True
        return b"\x00" in chunk

    def get_file_history(
        self, path: Path, limit: int = 10, cancel_token: CancelToken | None = None
    ) -> FileHistoryResult:
        """Last `limit` commits that touched `path` (repo-relative), newest
        first, following renames. Merges are excluded deliberately (see the
        module-level docstring on `_FILE_HISTORY_LOG_FORMAT`) -- accepted
        consequence: a merge that resolved a conflict does change the file
        and won't appear here, so the newest listed commit is not always the
        one that produced the file's current committed content.
        """
        if not self._has_any_commit():
            # `git log` on a zero-commit repo exits 128 with a message naming
            # the branch ("your current branch 'main' does not have any
            # commits yet") -- but list_tracked_files works fine before the
            # first commit, so a user can genuinely reach this by picking a
            # staged-but-never-committed file. That must render as "no
            # commits yet", not as a fatal error, so it's checked up front
            # via `git rev-parse --verify -q HEAD` rather than by pattern-
            # matching a message that embeds the branch name and is subject
            # to git-version/translation drift. current_path falls back to
            # the queried path here too -- same "no entries at all" rule the
            # general derivation below applies.
            return FileHistoryResult(current_path=path)

        args = [
            git.Git.GIT_PYTHON_GIT_EXECUTABLE,
            "-c",
            "core.quotePath=false",
            "log",
            "--follow",
            "--no-merges",
            "-n",
            str(limit),
            "--name-status",
            "-M",
            f"--format={_FILE_HISTORY_LOG_FORMAT}",
            "--",
            str(path),
        ]
        result = self._run_git_cancellable(args, cancel_token)
        if result.returncode != 0:
            raise git.GitCommandError(args, result.returncode, result.stderr)

        entries = self._parse_file_history_log(result.stdout.decode("utf-8", errors="replace"))

        if not entries:
            current_path = path
        elif entries[0].change_type == ChangeType.DELETED:
            current_path = None
        else:
            current_path = entries[0].path_at_commit

        return FileHistoryResult(entries=entries, current_path=current_path)

    @staticmethod
    def _parse_file_history_log(output: str) -> list[FileHistoryCommit]:
        """Parses `_FILE_HISTORY_LOG_FORMAT` + `--name-status` output.

        Deliberately NOT `-z`, and deliberately not `_parse_name_status_z` --
        this is the one place the repo's `-z` convention makes things worse.
        `--name-status -z` NUL-terminates *its own* fields with the same byte
        the format sentinels use, making record width variable (a 1-file
        commit yields 9 tokens, a rename 10) and forcing a parser to
        recognise a 40-hex SHA just to find a record boundary.

        Without `-z`, the whole stdout splits on NUL into `1 + 6*N` tokens --
        a leading empty one (the format string starts with `%x00`), then
        groups of exactly six, regardless of multi-line bodies, blank lines
        in a body, or rename lines: (hexsha, author, committed_iso, subject,
        body, name_status_block). The sixth token holds
        `"\\n\\n<tab-separated status line>\\n"` -- parsed directly below.
        """
        tokens = output.split("\0")
        entries: list[FileHistoryCommit] = []
        # tokens[0] is the leading empty token; records start at index 1.
        for i in range(1, len(tokens) - 5, 6):
            hexsha, author, committed_iso, subject, _body, name_status_block = tokens[i : i + 6]
            status_lines = [line for line in name_status_block.splitlines() if line.strip()]
            if not status_lines:
                # A record with no status line and a silently-defaulted
                # "reuse the previous entry's path" would hand back a wrong
                # path_at_commit with no exception -- raising here is what
                # makes that failure mode loud instead.
                raise ValueError(
                    f"git log --name-status produced no status line for commit {hexsha}"
                )
            # This call always filters by a single pathspec (--follow -- <path>),
            # so exactly one status line is expected per commit.
            columns = status_lines[0].split("\t")
            code = columns[0]
            if code.startswith("R"):
                renamed_from = Path(columns[1])
                path_at_commit = Path(columns[2])
                change_type = ChangeType.RENAMED
            else:
                renamed_from = None
                path_at_commit = Path(columns[1])
                change_type = {
                    "A": ChangeType.ADDED,
                    "D": ChangeType.DELETED,
                }.get(code[0], ChangeType.MODIFIED)

            entries.append(
                FileHistoryCommit(
                    commit=CommitLogEntry(
                        hexsha=hexsha,
                        short_hexsha=hexsha[:8],
                        message=subject,
                        committed_datetime=datetime.fromisoformat(committed_iso),
                        full_message=_body.strip(),
                        author=author,
                    ),
                    path_at_commit=path_at_commit,
                    change_type=change_type,
                    renamed_from=renamed_from,
                )
            )
        return entries

    def get_file_diff_against_disk(
        self,
        commit_hexsha: str,
        path_at_commit: Path,
        current_path: Path | None,
        cancel_token: CancelToken | None = None,
    ) -> DiffResult:
        """Mode B: `path_at_commit`'s content at `commit_hexsha` versus
        `current_path`'s content on disk *now* (unsaved editor buffers are
        never consulted). `current_path` is `None`, or names a path that no
        longer exists on disk, when the file has been deleted since --
        rendered as a full deletion (diffed against `/dev/null`) rather than
        as an error.
        """
        historical_args = [
            git.Git.GIT_PYTHON_GIT_EXECUTABLE,
            "cat-file",
            "-p",
            f"{commit_hexsha}:{path_at_commit.as_posix()}",
        ]
        historical_result = self._run_git_cancellable(historical_args, cancel_token)
        if historical_result.returncode != 0:
            raise git.GitCommandError(
                historical_args, historical_result.returncode, historical_result.stderr
            )
        historical_bytes: bytes = historical_result.stdout

        current_abs_path = self._repo_path / current_path if current_path is not None else None
        current_exists = current_abs_path is not None and current_abs_path.exists()
        current_bytes = current_abs_path.read_bytes() if current_exists else b""

        # `--no-index` on a binary pair emits "Binary files ... differ" with
        # zero hunks, which parses into a valid *empty* DiffResult -- a
        # silent blank pane rather than an error. Sniffing the full content
        # (already in memory either way) heads that off with a placeholder.
        if b"\x00" in historical_bytes or b"\x00" in current_bytes:
            return self._text_summary_result(["Binary file -- content not shown."])

        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                tmp_file.write(historical_bytes)
                tmp_path = Path(tmp_file.name)

            target = str(current_abs_path) if current_exists else "/dev/null"
            diff_args = [
                git.Git.GIT_PYTHON_GIT_EXECUTABLE,
                "diff",
                "--no-color",
                "--no-index",
                "--unified=100000",
                "--",
                str(tmp_path),
                target,
            ]
            diff_result = self._run_git_cancellable(diff_args, cancel_token)
            # --no-index returns 0 for identical content, 1 for any
            # difference -- both are success, and 0 is the *ordinary* result
            # here (mode B on a file's newest commit with no local edits),
            # not an edge case, so it must render as an empty diff rather
            # than as an error. Only >1 means diff itself couldn't run. This
            # is the opposite of _diff_new_file's check just below in this
            # file: a "new file" diff always has content, so that path never
            # sees 0 -- don't copy its ">0 means failure" comment here.
            if diff_result.returncode > 1:
                raise git.GitCommandError(diff_args, diff_result.returncode, diff_result.stderr)
            raw = diff_result.stdout.decode("utf-8", errors="replace")
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

        return self.parse_unified_diff(
            raw,
            old_ref=f"{commit_hexsha[:8]}:{path_at_commit}",
            new_ref="working tree" if current_exists else "(deleted)",
        )

    def _has_any_commit(self) -> bool:
        try:
            self._repo.git.rev_parse("--verify", "-q", "HEAD")
        except git.GitCommandError:
            return False
        return True

    def _run_git_cancellable(
        self, args: list[str], cancel_token: CancelToken | None
    ) -> subprocess.CompletedProcess:
        """Runs `args` (binary-mode, matching `CancelToken.run`'s own
        capture) via `cancel_token` when one is given, or directly when not
        -- same output shape either way, so callers never need two code
        paths depending on whether cancellation is wired up.

        The most likely silent failure this guards against: routing a git
        call through `self._repo.git.*` instead of through this, which would
        make `cancel()` a no-op that still looks like it worked while the
        operation runs to completion.
        """
        if cancel_token is not None:
            return cancel_token.run(args, cwd=self._repo_path)
        return subprocess.run(args, cwd=self._repo_path, capture_output=True)

    def get_remote_url(self, name: str = "origin") -> str | None:
        try:
            return self._repo.remotes[name].url
        except (IndexError, KeyError):
            return None

    def list_worktrees(self) -> list[Path]:
        try:
            output = self._repo.git.worktree("list", "--porcelain")
        except git.GitCommandError:
            return []

        worktrees: list[Path] = []
        for line in output.splitlines():
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree ").strip())
                if path.resolve() != self._repo_path.resolve():
                    worktrees.append(path)
        return worktrees

    def list_worktree_details(self) -> list[WorktreeInfo]:
        details: list[WorktreeInfo] = []
        for path in self.list_worktrees():
            if not path.exists():
                # Stale administrative entry for a worktree removed outside
                # the app (e.g. `rm -rf` instead of `git worktree remove`) --
                # nothing on disk left to report on.
                continue
            adapter = GitRepoAdapter(path)
            try:
                branch_name = adapter.get_branch_status().branch_name
            except (git.GitCommandError, IndexError):
                branch_name = ""
            last_activity: datetime | None = None
            try:
                commits = adapter.get_recent_commits(limit=1)
                if commits:
                    last_activity = commits[0].committed_datetime
            except git.GitCommandError:
                pass
            try:
                changes = adapter.list_changes()
            except git.GitCommandError:
                changes = []
            for change in changes:
                # A dirty working tree can be more recent than the last
                # commit -- e.g. an uncommitted edit made just now on top of
                # a week-old commit -- so the reported activity time is
                # whichever of the two is newer, not the commit time alone.
                mtime = GitRepoAdapter._get_modification_time(path / change.path)
                if mtime is not None and (last_activity is None or mtime > last_activity):
                    last_activity = mtime
            try:
                has_unpushed = adapter.has_unpushed_changes()
            except git.GitCommandError:
                has_unpushed = False
            details.append(
                WorktreeInfo(
                    path=path,
                    branch_name=branch_name,
                    last_activity=last_activity,
                    has_unpushed_changes=has_unpushed,
                    created_at=self._get_creation_time(path),
                )
            )
        return details

    def has_unpushed_changes(self) -> bool:
        # Ignored paths (a worktree's own node_modules, build output) are
        # excluded deliberately: they can never be pushed, so counting them
        # would flag every worktree "Yes" while every view that lists the
        # files -- all of which drop ignored entries -- shows nothing.
        if any(change.change_type != ChangeType.IGNORED for change in self.list_changes()):
            return True

        base = self._get_unpushed_diff_base()
        if base is None:
            # No upstream configured and no default branch to compare
            # against -- any commit here has nowhere it could have been
            # pushed to, so a repo with at least one commit counts as
            # unpushed.
            try:
                return bool(self._repo.head.commit)
            except (ValueError, TypeError):
                return False

        try:
            output = self._repo.git.rev_list("--count", f"{base}..HEAD")
        except git.GitCommandError:
            return False
        return int(output.strip() or "0") > 0

    def remove_worktree(self, path: Path, force: bool = False) -> None:
        args = ["remove", str(path)]
        if force:
            args.append("--force")
        self._repo.git.worktree(*args)

    def list_stashes(self) -> list[StashEntry]:
        """Lists this repo's stash entries, newest first (git's own natural
        order for `git stash list`).

        Uses an explicit NUL-separated `--pretty=format:` rather than the
        default one-line-per-entry text: a stash message routinely contains
        `:` (git's own default "On <branch>: <msg>" prefix) and can contain
        `|` or anything else the user typed, so any human-readable delimiter
        risks corrupting the split -- NUL is the one byte that can never
        appear in any of these fields (ref, subject, ISO date, author name).
        """
        try:
            output = self._repo.git.stash(
                "list", "--pretty=format:%gd%x00%gs%x00%aI%x00%an"
            )
        except git.GitCommandError:
            return []

        entries: list[StashEntry] = []
        for line in output.splitlines():
            if not line:
                continue
            ref, message, date_str, author = line.split("\x00")
            created_at = datetime.fromisoformat(date_str) if date_str else None
            entries.append(
                StashEntry(ref=ref, message=message, created_at=created_at, author=author)
            )
        return entries

    def stash_diff(self, ref: str) -> str:
        self._validate_stash_ref(ref)
        try:
            return self._repo.git.stash(
                "show", "--patch", "--no-color", "--include-untracked", ref
            )
        except git.GitCommandError:
            # Older git builds don't support --include-untracked on `stash
            # show` -- fall back to the plain (tracked-only) diff rather
            # than surfacing that flag's own error to the user.
            return self._repo.git.stash("show", "--patch", "--no-color", ref)

    def apply_stash(self, ref: str) -> None:
        self._validate_stash_ref(ref)
        self._repo.git.stash("apply", ref)

    def pop_stash(self, ref: str) -> None:
        self._validate_stash_ref(ref)
        self._repo.git.stash("pop", ref)

    def drop_stash(self, ref: str) -> None:
        self._validate_stash_ref(ref)
        self._repo.git.stash("drop", ref)

    def restore_file_from_stash(self, ref: str, path: Path) -> None:
        self._validate_stash_ref(ref)
        self._repo.git.checkout(ref, "--", path.as_posix())

    @staticmethod
    def _validate_stash_ref(ref: str) -> None:
        # Every ref this adapter is asked to act on comes from parsing this
        # adapter's own `list_stashes()` output -- this guards against a
        # malformed/hostile ref (e.g. "; rm -rf /") ever reaching git as an
        # argument, by rejecting anything that isn't exactly "stash@{N}"
        # before it's passed along.
        if not _STASH_REF_RE.match(ref):
            raise ValueError(f"Invalid stash ref: {ref!r}")

    @staticmethod
    def _get_creation_time(path: Path) -> datetime | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        # st_birthtime (true creation time) is only available on some
        # platforms (e.g. macOS/BSD); elsewhere this falls back to
        # st_ctime, which on Linux is metadata-change time, not creation --
        # the best available approximation there.
        timestamp = getattr(stat, "st_birthtime", None)
        if timestamp is None:
            timestamp = stat.st_ctime
        return datetime.fromtimestamp(timestamp).astimezone()

    @staticmethod
    def _get_modification_time(path: Path) -> datetime | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return datetime.fromtimestamp(stat.st_mtime).astimezone()

    def _find_default_branch(self) -> str | None:
        # Memoized: this is called once per get_branch_status() invocation,
        # and get_branch_status() runs once per repo per scan, but a caller
        # querying the same adapter twice (or the ls-remote fallback below
        # firing) shouldn't pay for the lookup — and the network fallback in
        # particular — more than once per adapter instance.
        if self._default_branch_cache is not _NOT_COMPUTED:
            return self._default_branch_cache  # type: ignore[return-value]
        result = self._find_default_branch_uncached()
        self._default_branch_cache = result
        return result

    def _find_default_branch_uncached(self) -> str | None:
        # `ls-remote` is the only source here that actually asks the remote
        # right now, so it's the only one that's authoritative — but it's a
        # real network round trip (~2.3s measured on this box), which is why
        # its answer is cached to disk per remote URL rather than skipped:
        # skipping it (e.g. by trusting the local symref below instead) can
        # return a wrong answer, not just a slow one (see the comment on
        # _read_local_origin_head_symref). Paying ~2.3s once per remote per
        # _DEFAULT_BRANCH_CACHE_TTL_SECONDS is an acceptable trade against
        # paying it on every scan of every repo on that remote.
        remote_url = self.get_remote_url("origin")
        if remote_url is not None:
            cached = workspace_cache.load_default_branch(
                remote_url, max_age_seconds=_DEFAULT_BRANCH_CACHE_TTL_SECONDS
            )
            if cached is not None:
                return cached

            resolved = self._ls_remote_default_branch()
            if resolved is not None:
                workspace_cache.save_default_branch(remote_url, resolved)
                return resolved

        # Everything below is a local-only hint, reachable only once BOTH
        # the cache and a live network probe (immediately above) have
        # already failed — some stale-but-plausible answer beats none.
        local_symref_hint = self._read_local_origin_head_symref()
        if local_symref_hint is not None:
            return local_symref_hint

        # Absolute last resort. On a machine whose git ships a system-level
        # gitconfig (e.g. Apple's Command Line Tools set
        # init.defaultbranch=main), this can return a plausible-looking
        # wrong answer -- but by this point the cache, a live network probe,
        # and the local symref have all already failed to produce anything,
        # so there is nothing more authoritative left to prefer over it.
        try:
            return self._repo.git.config("init.defaultBranch")
        except git.GitCommandError:
            return None

    def _ls_remote_default_branch(self, runner: LsRemoteRunner = subprocess.run) -> str | None:
        # Bounded so an unreachable/auth-prompting remote can never block a
        # scan indefinitely (this used to run with no timeout at all).
        #
        # Deliberately NOT GitPython's `kill_after_timeout=`: its watchdog
        # thread finds the child to kill by shelling out to
        # `ps --ppid <pid>` (git/cmd.py), and `--ppid` is a GNU/Linux-only ps
        # flag. On macOS's BSD ps that fails and prints a usage block to
        # stderr on *every* timeout -- exactly the noise this app must not
        # produce. Running git ourselves via subprocess.run(timeout=...) gets
        # the same bound with no `ps` invocation at all.
        try:
            result = runner(
                [
                    git.Git.GIT_PYTHON_GIT_EXECUTABLE,
                    "ls-remote",
                    "--symref",
                    "origin",
                    "HEAD",
                ],
                # self._repo_path over self._repo.working_dir/git_dir: this
                # adapter is only ever constructed against a working tree
                # (never a bare repo), and repo_path is already the exact
                # directory the caller pointed us at.
                cwd=self._repo_path,
                capture_output=True,
                text=True,
                timeout=_LS_REMOTE_TIMEOUT_SECONDS,
                # An auth-prompting remote must never sit there waiting on
                # input -- stdin is unreachable in a background scan, so
                # closing it (and telling git the same via the env var) turns
                # what would otherwise be a hang into an immediate failure.
                stdin=subprocess.DEVNULL,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if line.startswith("ref:"):
                ref = line.split()[1]
                return ref.removeprefix("refs/heads/")
        return None

    def _read_local_origin_head_symref(self) -> str | None:
        # `refs/remotes/origin/HEAD` is a local symbolic ref written once by
        # `git clone` (or an explicit `git remote set-head`). Unlike a
        # branch ref, `git fetch` does NOT refresh it, so it's a snapshot
        # that silently goes stale the moment the remote's actual default
        # branch changes — measured disagreeing with `ls-remote` on both of
        # the real repos this was tested against (dashboard: cached
        # "develop", remote is really "main"; server: cached "main", remote
        # is really "release/3.6.6"). That staleness is exactly why this is
        # ranked below the cache and the network probe, never above them.
        try:
            ref = self._repo.git.symbolic_ref("refs/remotes/origin/HEAD")
        except git.GitCommandError:
            return None
        # ref looks like "refs/remotes/origin/main". Branch names here
        # routinely contain '/' (e.g. "TASK/foo-bar"), so strip the known
        # prefix rather than splitting/partitioning on '/'.
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix):
            return ref.removeprefix(prefix)
        return None

    def _find_local_parent_branch(self, branch_name: str) -> str | None:
        try:
            current = self._repo.heads[branch_name]
        except (IndexError, KeyError):
            return None

        other_heads = [head for head in self._repo.heads if head.name != branch_name]
        if not other_heads:
            return None

        # The obvious version of this loop calls `git merge-base` once per
        # other local branch (plus a `committed_date` lookup on each
        # result). That's ~1 subprocess spawn per branch — on a repo with
        # ~105 local branches, measured at ~2.3-3.7s total, because forking
        # a large GUI process is itself expensive on this box regardless of
        # what the child does. Dumping the whole local-branch commit graph
        # with a single `git rev-list` call and computing every merge base
        # as a pure-Python graph query turns that into exactly one
        # subprocess no matter how many branches exist.
        try:
            dump = self._repo.git.rev_list(
                "--branches", "--topo-order", "--parents", pretty="format:%H %ct"
            )
        except git.GitCommandError:
            return None

        parents: dict[str, list[str]] = {}
        children: dict[str, list[str]] = {}
        committed_at: dict[str, int] = {}
        lines = dump.splitlines()
        # Output alternates "commit <sha> <parents...>" / "<sha> <ct>" per
        # commit (see git-rev-list(1) --parents + --pretty=format combo);
        # the format string is deliberately just hash+timestamp (no subject
        # line) so neither line can itself contain embedded newlines.
        for i in range(0, len(lines) - 1, 2):
            header = lines[i].split()
            sha, parent_shas = header[1], header[2:]
            parents[sha] = parent_shas
            for parent_sha in parent_shas:
                children.setdefault(parent_sha, []).append(sha)
            _, ct = lines[i + 1].split()
            committed_at[sha] = int(ct)

        def ancestors(start_sha: str) -> set[str]:
            seen = {start_sha}
            stack = [start_sha]
            while stack:
                node = stack.pop()
                for parent_sha in parents.get(node, ()):
                    if parent_sha not in seen:
                        seen.add(parent_sha)
                        stack.append(parent_sha)
            return seen

        current_ancestors = ancestors(current.commit.hexsha)

        best_branch: str | None = None
        best_commit_time = -1
        for head in other_heads:
            common = ancestors(head.commit.hexsha) & current_ancestors
            if not common:
                continue
            # The real merge base is the maximal element(s) of the common-
            # ancestor set — a common ancestor with no descendant that is
            # also a common ancestor. `common` itself is ancestor-closed
            # (an ancestor of a common ancestor is common too), so picking
            # "any" element would usually pick something far too old; this
            # is what makes the result match `git merge-base` even across
            # merge commits, not just on linear history.
            merge_base_candidates = [
                sha
                for sha in common
                if not any(child in common for child in children.get(sha, ()))
            ]
            commit_time = max(committed_at[sha] for sha in merge_base_candidates)
            if commit_time > best_commit_time:
                best_commit_time = commit_time
                best_branch = head.name

        return best_branch

    def compute_diff(self, change: FileChange, ignore_whitespace: bool = False) -> DiffResult:
        if change.change_type == ChangeType.UNTRACKED:
            return self._diff_untracked(change)

        args = ["--no-color", "-M", "--unified=100000"]
        if ignore_whitespace:
            args.append("--ignore-all-space")

        if change.is_unpushed_commit:
            base = self._get_unpushed_diff_base() or "HEAD"
            args.append(f"{base}...HEAD")
            old_ref, new_ref = base, "HEAD"
        else:
            args.append("HEAD")
            old_ref, new_ref = "HEAD", "working tree"

        args.append("--")
        if change.old_path:
            args.append(str(change.old_path))
        args.append(str(change.path))

        raw = self._repo.git.diff(*args)
        return self.parse_unified_diff(raw, old_ref=old_ref, new_ref=new_ref)

    def _diff_untracked(self, change: FileChange) -> DiffResult:
        """Renders an untracked path's content as an all-ADDED "diff".

        `git status` collapses an untracked *directory* into one porcelain
        entry (e.g. `node_modules/`) instead of one line per file inside it,
        so `change.path` can name a directory rather than a file — reading
        that as text raised IsADirectoryError (the bug this guards against).
        Two more disk-reality mismatches get the same "never crash, show a
        readable line instead" treatment here rather than at the GUI layer,
        since both are specific to *this* path (reading arbitrary bytes off
        disk) and not to diffing in general: an untracked binary file, whose
        raw bytes would otherwise render as decode-replacement garbage
        instead of a message; and a path that vanished from disk between the
        workspace scan and the click that requested its diff.
        """
        abs_path = self._repo_path / change.path
        # `change.is_directory` is the normal signal (list_changes already
        # stat'd it once); is_dir() here is a defensive fallback for a stale
        # FileChange (change.is_directory computed before something on disk
        # was swapped for a directory) rather than a second stat on the
        # common path.
        if change.is_directory or abs_path.is_dir():
            return self._summarize_untracked_directory(abs_path)

        try:
            size = abs_path.stat().st_size
        except FileNotFoundError:
            return self._text_summary_result([f"File no longer exists on disk: {change.path}"])
        if size > _UNTRACKED_FILE_DIFF_MAX_BYTES:
            return self._text_summary_result(
                [f"File too large to preview ({size:,} bytes) — content not shown."]
            )

        try:
            raw_bytes = abs_path.read_bytes()
        except FileNotFoundError:
            return self._text_summary_result([f"File no longer exists on disk: {change.path}"])
        except IsADirectoryError:
            # Race: became a directory after the is_dir() check above.
            return self._summarize_untracked_directory(abs_path)

        # Git's own binary-detection heuristic (a NUL byte anywhere in the
        # sample) — good enough here since the alternative is only "readable
        # message" vs. "decode-replacement mojibake", not an apply-able diff.
        if b"\x00" in raw_bytes:
            return self._text_summary_result(
                [f"Binary file ({len(raw_bytes)} bytes) — content not shown."]
            )

        content = raw_bytes.decode("utf-8", errors="replace")
        lines = content.splitlines()
        return self._text_summary_result(lines, start_lineno=1)

    def _summarize_untracked_directory(self, abs_path: Path) -> DiffResult:
        """An untracked directory can hold tens of thousands of files (a
        `node_modules` is exactly the case that broke this) — reading each one
        into the diff pane would be slow and useless, so this reports a
        bounded summary instead: how many files, and the first few names.
        `_UNTRACKED_DIR_SUMMARY_SCAN_LIMIT` caps the walk itself, not just the
        displayed list, so a pathological tree can't stall the UI either.
        """
        names: list[str] = []
        total = 0
        capped = False
        for root, _dirs, files in os.walk(abs_path):
            for name in sorted(files):
                total += 1
                if total > _UNTRACKED_DIR_SUMMARY_SCAN_LIMIT:
                    capped = True
                    break
                if len(names) < _UNTRACKED_DIR_SUMMARY_MAX_NAMES:
                    names.append(str((Path(root) / name).relative_to(abs_path)))
            if capped:
                break

        count_label = f"{_UNTRACKED_DIR_SUMMARY_SCAN_LIMIT}+" if capped else str(total)
        lines = [f"Untracked directory with {count_label} file(s) — showing a summary, not a diff:"]
        lines.extend(f"  {name}" for name in names)
        if capped:
            lines.append(f"  ... and more (stopped counting after {_UNTRACKED_DIR_SUMMARY_SCAN_LIMIT})")
        elif total > len(names):
            lines.append(f"  ... and {total - len(names)} more")
        return self._text_summary_result(lines)

    @staticmethod
    def _text_summary_result(lines: list[str], start_lineno: int = 1) -> DiffResult:
        """Wraps plain text lines as a one-hunk, all-ADDED DiffResult — the
        shape every diff view already knows how to render, reused here for
        the untracked-path summary/placeholder messages (directory, binary,
        vanished-file) so they need no rendering path of their own.
        """
        hunk_lines = [
            DiffLine(kind=DiffLineKind.ADDED, old_lineno=None, new_lineno=i, text=text)
            for i, text in enumerate(lines, start=start_lineno)
        ]
        hunks = []
        if hunk_lines:
            hunks.append(
                DiffHunk(old_start=0, old_count=0, new_start=1, new_count=len(lines), lines=hunk_lines)
            )
        return DiffResult(old_ref="(none)", new_ref="working tree", hunks=hunks)

    @staticmethod
    def parse_unified_diff(raw: str, old_ref: str, new_ref: str) -> DiffResult:
        hunks: list[DiffHunk] = []
        current_hunk: DiffHunk | None = None
        old_lineno = new_lineno = 0
        old_blob_id: str | None = None
        new_blob_id: str | None = None

        for line in raw.splitlines():
            index_match = _INDEX_LINE_RE.match(line)
            if index_match:
                old_blob_id, new_blob_id = index_match.group(1), index_match.group(2)
                continue

            match = _HUNK_HEADER_RE.match(line)
            if match:
                old_start = int(match.group(1))
                new_start = int(match.group(3))
                current_hunk = DiffHunk(
                    old_start=old_start,
                    old_count=int(match.group(2) or "1"),
                    new_start=new_start,
                    new_count=int(match.group(4) or "1"),
                    lines=[],
                )
                hunks.append(current_hunk)
                old_lineno = old_start
                new_lineno = new_start
                continue

            if current_hunk is None or line.startswith("\\"):
                continue

            if line.startswith("+"):
                current_hunk.lines.append(
                    DiffLine(DiffLineKind.ADDED, None, new_lineno, line[1:])
                )
                new_lineno += 1
            elif line.startswith("-"):
                current_hunk.lines.append(
                    DiffLine(DiffLineKind.REMOVED, old_lineno, None, line[1:])
                )
                old_lineno += 1
            elif line.startswith(" "):
                current_hunk.lines.append(
                    DiffLine(DiffLineKind.CONTEXT, old_lineno, new_lineno, line[1:])
                )
                old_lineno += 1
                new_lineno += 1

        return DiffResult(
            old_ref=old_ref,
            new_ref=new_ref,
            hunks=hunks,
            old_blob_id=old_blob_id,
            new_blob_id=new_blob_id,
        )

    @staticmethod
    def _classify(xy: str) -> ChangeType:
        if xy in _STATUS_CODE_TO_CHANGE_TYPE:
            return _STATUS_CODE_TO_CHANGE_TYPE[xy]
        if "R" in xy:
            return ChangeType.RENAMED
        if "A" in xy:
            return ChangeType.ADDED
        if "D" in xy:
            return ChangeType.DELETED
        return ChangeType.MODIFIED

    def build_patch(self, tracked_paths: list[Path], untracked_paths: list[Path]) -> str:
        """Builds a raw, `git apply`-able unified diff covering exactly the tracked
        files named in `tracked_paths` and the untracked files named in
        `untracked_paths` -- an explicit selection (e.g. the "Create patch" dialog's
        checked rows) rather than "everything under some target path", so a caller
        can hand it a narrowed-down subset without this method re-deriving scope
        from a directory itself.

        Built straight from git rather than from `DiffResult`/`diff_formatting.py`:
        those exist to *render* a diff (parsed hunks, `--unified=100000` full-file
        context for the side-by-side view, staged/whitespace variants) and throw the
        raw text away, and `diff_formatting.py`'s reconstruction has no `diff --git`
        envelope and is deliberately not apply-able. A patch that has to round-trip
        through `git apply` needs git's own patch text, not a re-render of data that
        was shaped for the viewer.

        Tracked changes come from a single `git diff HEAD -- <path> <path> ...`
        (one pathspec per selected tracked file), so both staged and unstaged edits
        land in one patch and only the named files are covered. Untracked files
        aren't in git's index at all, so they can't go through that same diff —
        each is generated separately via `git diff --no-index -- /dev/null <file>`,
        which is also how `git apply` learns to create a brand-new file rather than
        patch one that doesn't exist yet.
        """
        tracked = ""
        if tracked_paths:
            tracked = self._repo.git.diff(
                "--no-color", "HEAD", "--", *(str(path) for path in tracked_paths)
            )
        if tracked and not tracked.endswith("\n"):
            tracked += "\n"

        untracked_chunks: list[str] = []
        for path in untracked_paths:
            abs_path = self._repo_path / path
            # An untracked *directory* is one collapsed FileChange (see
            # list_changes/tree_model), but `git diff --no-index` only knows how to
            # diff a file against /dev/null — so expand it to its actual files here.
            files = sorted(p for p in abs_path.rglob("*") if p.is_file()) if abs_path.is_dir() else [abs_path]
            for file_path in files:
                chunk = self._diff_new_file(file_path.relative_to(self._repo_path))
                if chunk:
                    untracked_chunks.append(chunk)

        return tracked + "".join(untracked_chunks)

    def apply_patch(self, patch_text: str, selected_paths: Collection[Path]) -> None:
        """Applies `patch_text` to this repo's working tree, restricted to
        `selected_paths` (one `--include=<posix path>` per selected file, so
        an unchecked file in the patch never gets touched even though it's
        still part of the same patch text).

        Fed on stdin rather than through a temp file -- `subprocess.run`'s
        `input=` (not GitPython's `self._repo.git.apply(istream=...)`, which
        needs a real file object with a `fileno()` and can't take a plain
        string/StringIO; verified against the installed GitPython by hand).
        This mirrors `_diff_new_file`'s existing subprocess-over-GitPython
        precedent below.

        `git.Repo.git.diff(...)` (as `build_patch` above uses to build a
        patch) strips the trailing newline from its output, and `git apply`
        then reads the final hunk line as truncated ("corrupt patch") --
        verified by hand against this GitPython version. Any patch text this
        method receives could have come from exactly that path (round-
        tripped through "Create patch" -> saved to disk -> re-applied here),
        so a missing trailing newline is restored defensively before either
        git-apply invocation, rather than trusting every caller to have kept
        one.

        Runs `--check` first and only proceeds to the real apply if that
        succeeds, so a bad patch (or a selection git can't cleanly apply)
        raises before touching the working tree at all rather than leaving
        it half-patched.
        """
        text = patch_text
        if text and not text.endswith("\n"):
            text += "\n"
        include_args = [f"--include={path.as_posix()}" for path in selected_paths]

        self._run_git_apply(["--check", *include_args], text)
        self._run_git_apply(include_args, text)

    def _run_git_apply(self, args: list[str], patch_text: str) -> None:
        command = ["git", "apply", *args]
        try:
            result = subprocess.run(
                command,
                cwd=self._repo_path,
                input=patch_text,
                text=True,
                capture_output=True,
            )
        except OSError as error:
            raise git.GitCommandError(command, 1, str(error)) from error
        if result.returncode != 0:
            raise git.GitCommandError(command, result.returncode, result.stderr)

    def _diff_new_file(self, relpath: Path) -> str:
        try:
            result = subprocess.run(
                ["git", "diff", "--no-color", "--no-index", "--", "/dev/null", str(relpath)],
                cwd=self._repo_path,
                capture_output=True,
                text=True,
            )
        except OSError:
            # Unreadable path (permissions, dangling symlink, etc) — skip it rather
            # than fail the whole folder patch over one file the user can't see anyway.
            return ""
        # --no-index exits 1 whenever it finds a difference (i.e. always, for a
        # real new file), so 1 is success here, not failure. >1 means diff itself
        # couldn't run (bad path). A binary diff has no textual patch content git
        # apply could use, so skip it rather than emit a header with nothing to apply.
        if result.returncode > 1 or "Binary files" in result.stdout:
            return ""
        return result.stdout
