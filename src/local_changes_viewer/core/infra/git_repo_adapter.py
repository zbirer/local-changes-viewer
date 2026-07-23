import re
from pathlib import Path

import git

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.repository import BranchStatus

_BRANCH_LINE_RE = re.compile(
    r"^## (?P<branch>\S+?)(?:\.\.\.(?P<upstream>\S+))?(?: \[(?P<info>[^\]]+)\])?$"
)
_AHEAD_BEHIND_RE = re.compile(r"(ahead|behind) (\d+)")

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
