from pathlib import Path


class FileSystemScanner:
    def find_git_repos(self, root: Path) -> list[Path]:
        if self._is_git_repo(root):
            return [root]

        try:
            children = sorted(root.iterdir())
        except OSError:
            # `root` itself became unreadable (permission change, an
            # unmounted network share) between being picked as a workspace
            # folder and being scanned right now — nothing to report, but
            # not a reason to blow up the whole scan either.
            return []

        repos: list[Path] = []
        for child in children:
            try:
                is_repo = child.is_dir() and self._is_git_repo(child)
            except OSError:
                # One permission-denied entry under the workspace root (e.g.
                # another user's home directory bind-mounted alongside real
                # repos) must not abort scanning of every sibling folder —
                # skip it silently, same as any other non-repo folder. Not
                # logged: this codebase has no logging infrastructure, and a
                # skipped folder with no `.git` in it either way is
                # indistinguishable from "not a repo" to the caller.
                continue
            if is_repo:
                repos.append(child)
        return repos

    @staticmethod
    def _is_git_repo(path: Path) -> bool:
        return (path / ".git").exists()
