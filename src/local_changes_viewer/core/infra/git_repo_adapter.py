import re
from pathlib import Path

import git

from local_changes_viewer.core.domain.diff import DiffHunk, DiffLine, DiffLineKind, DiffResult
from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus

_BRANCH_LINE_RE = re.compile(
    r"^## (?P<branch>\S+?)(?:\.\.\.(?P<upstream>\S+))?(?: \[(?P<info>[^\]]+)\])?$"
)
_AHEAD_BEHIND_RE = re.compile(r"(ahead|behind) (\d+)")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

_STATUS_CODE_TO_CHANGE_TYPE = {
    "??": ChangeType.UNTRACKED,
    "!!": ChangeType.IGNORED,
}


class GitRepoAdapter:
    def __init__(self, repo_path: Path) -> None:
        self._repo_path = repo_path
        self._repo = git.Repo(repo_path)

    def list_changes(self) -> list[FileChange]:
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
            changes.append(
                FileChange(
                    path=Path(rest.strip()),
                    change_type=self._classify(xy),
                    old_path=old_path,
                )
            )
        return changes

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

        return BranchStatus(branch_name=match.group("branch"), ahead=ahead, behind=behind)

    def compute_diff(self, change: FileChange, ignore_whitespace: bool = False) -> DiffResult:
        if change.change_type == ChangeType.UNTRACKED:
            return self._diff_untracked(change.path)

        args = ["--no-color", "-M", "--unified=100000"]
        if ignore_whitespace:
            args.append("--ignore-all-space")
        args.append("HEAD")
        args.append("--")
        if change.old_path:
            args.append(str(change.old_path))
        args.append(str(change.path))

        raw = self._repo.git.diff(*args)
        return self._parse_unified_diff(raw, old_ref="HEAD", new_ref="working tree")

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

        for line in raw.splitlines():
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

        return DiffResult(old_ref=old_ref, new_ref=new_ref, hunks=hunks)

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
