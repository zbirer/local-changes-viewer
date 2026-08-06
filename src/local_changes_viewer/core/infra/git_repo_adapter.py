import re
from pathlib import Path

import git

from local_changes_viewer.core.domain.commit_log_entry import CommitLogEntry
from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus

_BRANCH_LINE_RE = re.compile(
    r"^## (?P<branch>\S+?)(?:\.\.\.(?P<upstream>\S+))?(?: \[(?P<info>[^\]]+)\])?$"
)
_AHEAD_BEHIND_RE = re.compile(r"(ahead|behind) (\d+)")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_INDEX_LINE_RE = re.compile(r"^index (\w+)\.\.(\w+)")

_STATUS_CODE_TO_CHANGE_TYPE = {
    "??": ChangeType.UNTRACKED,
    "!!": ChangeType.IGNORED,
}


class GitRepoAdapter:
    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path
        self._repo = git.Repo(repo_path)

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
            changes.append(
                FileChange(
                    path=Path(rest),
                    change_type=self._classify(xy),
                    old_path=old_path,
                    is_directory=rest.endswith("/"),
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
            )
            for commit in commits
        ]

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

    def _find_default_branch(self) -> str | None:
        try:
            output = self._repo.git.ls_remote("--symref", "origin", "HEAD")
            for line in output.splitlines():
                if line.startswith("ref:"):
                    ref = line.split()[1]
                    return ref.removeprefix("refs/heads/")
        except git.GitCommandError:
            pass
        try:
            return self._repo.git.config("init.defaultBranch")
        except git.GitCommandError:
            return None

    def _find_local_parent_branch(self, branch_name: str) -> str | None:
        try:
            current = self._repo.heads[branch_name]
        except (IndexError, KeyError):
            return None

        best_branch: str | None = None
        best_commit_time = -1
        for head in self._repo.heads:
            if head.name == branch_name:
                continue
            try:
                merge_bases = self._repo.merge_base(current, head)
            except git.GitCommandError:
                continue
            if not merge_bases:
                continue
            commit_time = merge_bases[0].committed_date
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
