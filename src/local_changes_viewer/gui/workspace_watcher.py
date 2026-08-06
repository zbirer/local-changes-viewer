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

_DEBOUNCE_MS = 400


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
        self._debounce_timer.timeout.connect(self.changed.emit)

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

    def _on_directory_changed(self, _path: str) -> None:
        self._debounce_timer.start()
