import os
from pathlib import Path


class FileSystemScanner:
    def find_git_repos(self, root: Path) -> list[Path]:
        repos: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            if ".git" in dirnames or ".git" in filenames:
                repos.append(current)
            # Don't descend into .git internals looking for further repos.
            dirnames[:] = [d for d in dirnames if d != ".git"]
        return repos
