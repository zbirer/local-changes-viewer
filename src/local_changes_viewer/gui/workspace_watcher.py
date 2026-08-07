import os
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

_IGNORED_DIR_NAMES = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    ".idea",
    ".vscode",
}

# A busy workspace (many repos under active development, e.g. build output or
# a running dev server touching files) can emit directoryChanged bursts for
# seconds at a time; 400ms wasn't enough to let that settle, so `changed` kept
# re-firing every ~2s and each one triggered a full rescan. 2000ms is enough
# for a typical burst to go quiet before we react to it (paired with the
# minimum-interval guard between auto-refresh scans in MainWindow).
_DEBOUNCE_MS = 2000


def collect_watch_paths(repo_paths: list[Path]) -> list[Path]:
    watch_paths: list[Path] = []
    for repo_path in repo_paths:
        for dirpath, dirnames, _filenames in os.walk(repo_path):
            dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIR_NAMES]
            watch_paths.append(Path(dirpath))
    return watch_paths


class WorkspaceFileWatcher(QObject):
    """Watches repo working directories and emits a debounced signal on changes."""

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_directory_changed)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._on_debounce_timeout)
        self._dirty_paths: set[Path] = set()

    def set_watch_paths(self, watch_paths: list[Path]) -> None:
        """Applies a precomputed path list; callers walk directories off-thread first."""
        existing = self._watcher.directories()
        if existing:
            self._watcher.removePaths(existing)
        if watch_paths:
            self._watcher.addPaths([str(p) for p in watch_paths])

    def stop(self) -> None:
        self._debounce_timer.stop()
        existing = self._watcher.directories()
        if existing:
            self._watcher.removePaths(existing)
        self._dirty_paths.clear()

    def dirty_repo_roots(self, repo_paths: list[Path]) -> set[Path]:
        """Maps paths that fired since the previous `changed` emit up to their
        owning repo root (whichever `repo_paths` entry is a parent of, or equal
        to, the fired path)."""
        dirty_roots: set[Path] = set()
        for dirty_path in self._dirty_paths:
            for repo_path in repo_paths:
                if dirty_path == repo_path or repo_path in dirty_path.parents:
                    dirty_roots.add(repo_path)
                    break
        return dirty_roots

    def _on_directory_changed(self, path: str) -> None:
        self._dirty_paths.add(Path(path))
        self._debounce_timer.start()

    def _on_debounce_timeout(self) -> None:
        self.changed.emit()
        self._dirty_paths.clear()
