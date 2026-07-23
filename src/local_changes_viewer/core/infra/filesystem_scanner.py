from pathlib import Path


class FileSystemScanner:
    def find_git_repos(self, root: Path) -> list[Path]:
        if self._is_git_repo(root):
            return [root]

        repos: list[Path] = []
        for child in sorted(root.iterdir()):
            if child.is_dir() and self._is_git_repo(child):
                repos.append(child)
        return repos

    @staticmethod
    def _is_git_repo(path: Path) -> bool:
        return (path / ".git").exists()
