import os
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from local_changes_viewer.gui import applog

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

# QFileSystemWatcher backs every watched path with an OS-level handle/inotify
# watch/kqueue descriptor; handing it an unbounded file list (a workspace with
# many repos, each with many already-changed files) risks exhausting those
# descriptors. This bounds the per-refresh file watch list; when the true
# count is larger, the truncated tail simply won't get instant fileChanged
# notification for an in-place edit (it still gets picked up by the age floor
# in WorkspaceScannerService, just not immediately).
_MAX_WATCHED_FILES = 2000


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
        # directoryChanged only fires on create/delete/rename inside a watched
        # directory — an editor that writes an already-tracked file in place
        # (no rename, same inode) never triggers it, which is exactly how a
        # repo's changes silently went stale forever. fileChanged closes that
        # gap for any path currently known to be changed (see set_watched_files).
        self._watcher.fileChanged.connect(self._on_file_changed)
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

    def set_watched_files(self, file_paths: list[Path]) -> None:
        """Watches individual (already-changed) file paths so an in-place edit
        to one of them marks its repo dirty immediately via fileChanged,
        instead of waiting for the next age-floor rescan. Re-register this
        whenever the change set is refreshed — call alongside set_watch_paths.
        """
        truncated = len(file_paths) > _MAX_WATCHED_FILES
        if truncated:
            file_paths = file_paths[:_MAX_WATCHED_FILES]
        existing = self._watcher.files()
        if existing:
            self._watcher.removePaths(existing)
        if file_paths:
            self._watcher.addPaths([str(p) for p in file_paths])
        if truncated:
            applog.log(
                f"File watcher: capped watched files at {_MAX_WATCHED_FILES} "
                "(more changed files than that were present)",
                level=applog.LogLevel.DEBUG,
            )

    def stop(self) -> None:
        self._debounce_timer.stop()
        existing = self._watcher.directories()
        if existing:
            self._watcher.removePaths(existing)
        existing_files = self._watcher.files()
        if existing_files:
            self._watcher.removePaths(existing_files)
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

    def _on_file_changed(self, path: str) -> None:
        # Same handling as a directory firing: mark it dirty and let the
        # existing debounce collapse a burst of edits into one `changed` emit.
        self._dirty_paths.add(Path(path))
        self._debounce_timer.start()

    def _on_debounce_timeout(self) -> None:
        self.changed.emit()
        self._dirty_paths.clear()
