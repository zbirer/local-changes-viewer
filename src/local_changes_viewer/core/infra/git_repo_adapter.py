import re
from datetime import datetime
from pathlib import Path

import git

from local_changes_viewer.core.domain.commit_log_entry import CommitLogEntry
from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus
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
        upstream = self._get_upstream_ref()
        if upstream is None:
            return []
        try:
            output = self._repo.git.diff("--no-color", "--name-status", "-M", f"{upstream}...HEAD")
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
                    commit_message=self._get_commit_messages(upstream, path),
                )
            )
        return changes

    def _get_commit_messages(self, upstream: str, path: Path) -> str | None:
        try:
            output = self._repo.git.log("--format=%s", f"{upstream}..HEAD", "--", str(path))
        except git.GitCommandError:
            return None
        return output.strip() or None

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
        return self._parse_unified_diff(
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

        upstream = self._get_upstream_ref()
        if upstream is None:
            # No upstream configured at all -- any commit here has nowhere
            # it could have been pushed to, so a repo with at least one
            # commit counts as unpushed.
            try:
                return bool(self._repo.head.commit)
            except (ValueError, TypeError):
                return False

        try:
            output = self._repo.git.rev_list("--count", f"{upstream}..HEAD")
        except git.GitCommandError:
            return False
        return int(output.strip() or "0") > 0

    def remove_worktree(self, path: Path, force: bool = False) -> None:
        args = ["remove", str(path)]
        if force:
            args.append("--force")
        self._repo.git.worktree(*args)

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

    def _ls_remote_default_branch(self) -> str | None:
        # Bounded so an unreachable/auth-prompting remote can never block a
        # scan indefinitely (this used to run with no timeout at all).
        try:
            output = self._repo.git.ls_remote(
                "--symref", "origin", "HEAD", kill_after_timeout=5
            )
        except git.GitCommandError:
            return None
        for line in output.splitlines():
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
            return self._diff_untracked(change.path)

        args = ["--no-color", "-M", "--unified=100000"]
        if ignore_whitespace:
            args.append("--ignore-all-space")

        if change.is_unpushed_commit:
            upstream = self._get_upstream_ref() or "HEAD"
            args.append(f"{upstream}...HEAD")
            old_ref, new_ref = upstream, "HEAD"
        else:
            args.append("HEAD")
            old_ref, new_ref = "HEAD", "working tree"

        args.append("--")
        if change.old_path:
            args.append(str(change.old_path))
        args.append(str(change.path))

        raw = self._repo.git.diff(*args)
        return self._parse_unified_diff(raw, old_ref=old_ref, new_ref=new_ref)

    def _diff_untracked(self, path: Path) -> DiffResult:
        content = (self._repo_path / path).read_text(errors="replace")
        lines = content.splitlines()
        hunk_lines = [
            DiffLine(kind=DiffLineKind.ADDED, old_lineno=None, new_lineno=i, text=text)
            for i, text in enumerate(lines, start=1)
        ]
        hunks = []
        if hunk_lines:
            hunks.append(
                DiffHunk(old_start=0, old_count=0, new_start=1, new_count=len(lines), lines=hunk_lines)
            )
        return DiffResult(old_ref="(none)", new_ref="working tree", hunks=hunks)

    @staticmethod
    def _parse_unified_diff(raw: str, old_ref: str, new_ref: str) -> DiffResult:
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
