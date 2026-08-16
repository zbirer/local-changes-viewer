import os
import re
import subprocess
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import Callable

import git

from local_changes_viewer.core.domain.commit_log_entry import CommitLogEntry
from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus
from local_changes_viewer.core.domain.stash_entry import StashEntry
from local_changes_viewer.core.domain.worktree_info import WorktreeInfo
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


class GitRepoAdapter:
    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path
        self._repo = git.Repo(repo_path)
        # _find_default_branch's network fallback is a real round trip, so its
        # result is memoized for the adapter's lifetime — a scan otherwise
        # pays for it once per call site even though it can't change mid-scan.
        self._default_branch_cache: object = _NOT_COMPUTED

    def list_changes(self, include_unpushed_commits: bool = False) -> list[FileChange]:
        output = self._repo.git.status("--porcelain=v1", "--ignored")
        changes: list[FileChange] = []
        for line in output.splitlines():
            if not line:
                continue
            xy = line[:2]
            rest = line[3:]
            old_path: Path | None = None
            if " -> " in rest:
                old_str, new_str = rest.split(" -> ", maxsplit=1)
                old_path = Path(old_str.strip())
                rest = new_str
            rest = rest.strip()
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
            output = self._repo.git.diff("--no-color", "--name-status", "-M", f"{base}...HEAD")
        except git.GitCommandError:
            return []

        changes: list[FileChange] = []
        for line in output.splitlines():
            if not line:
                continue
            parts = line.split("\t")
            code = parts[0]
            if code.startswith("R"):
                old_path, path = Path(parts[1]), Path(parts[2])
                change_type = ChangeType.RENAMED
            else:
                path = Path(parts[1])
                old_path = None
                change_type = {"A": ChangeType.ADDED, "D": ChangeType.DELETED}.get(
                    code[0], ChangeType.MODIFIED
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
            )
            for commit in commits
        ]

    def _get_branch_for_commit(self, hexsha: str) -> str:
        try:
            output = self._repo.git.branch("--contains", hexsha, "--format=%(refname:short)")
        except git.GitCommandError:
            return ""
        for line in output.splitlines():
            name = line.strip()
            if name:
                return name
        return ""

    def get_commit_files(self, commit_hexsha: str) -> list[FileChange]:
        output = self._repo.git.show(
            "--no-color", "--name-status", "--pretty=format:", "-M", commit_hexsha
        )
        changes: list[FileChange] = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            code = parts[0]
            if code.startswith("R"):
                old_path, path = Path(parts[1]), Path(parts[2])
                change_type = ChangeType.RENAMED
            else:
                path = Path(parts[1])
                old_path = None
                change_type = {"A": ChangeType.ADDED, "D": ChangeType.DELETED}.get(
                    code[0], ChangeType.MODIFIED
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
        if self.list_changes():
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
