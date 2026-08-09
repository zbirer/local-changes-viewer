from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from local_changes_viewer.gui.workspace_watcher import (
    WorkspaceFileWatcher,
    collect_watch_paths,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_collect_watch_paths_includes_repo_root_and_subdirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo_a"
    (repo / "src").mkdir(parents=True)

    result = collect_watch_paths([repo])

    assert repo in result
    assert repo / "src" in result


def test_collect_watch_paths_skips_ignored_directories(tmp_path: Path) -> None:
    repo = tmp_path / "repo_a"
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / ".git" / "objects").mkdir(parents=True)
    (repo / "src").mkdir(parents=True)

    result = collect_watch_paths([repo])

    assert repo / "src" in result
    assert not any("node_modules" in str(p) for p in result)
    assert not any(".git" in str(p) for p in result)


def test_collect_watch_paths_handles_multiple_repos(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    repo_a.mkdir()
    repo_b.mkdir()

    result = collect_watch_paths([repo_a, repo_b])

    assert repo_a in result
    assert repo_b in result


def test_dirty_repo_roots_maps_subdirectory_change_to_owning_repo_root() -> None:
    watcher = WorkspaceFileWatcher()
    repo_a = Path("/workspace/repo_a")
    repo_b = Path("/workspace/repo_b")

    watcher._on_directory_changed(str(repo_a / "src" / "nested"))

    assert watcher.dirty_repo_roots([repo_a, repo_b]) == {repo_a}


def test_dirty_repo_roots_ignores_paths_outside_known_repo_roots() -> None:
    watcher = WorkspaceFileWatcher()
    repo_a = Path("/workspace/repo_a")
    repo_b = Path("/workspace/repo_b")

    watcher._on_directory_changed(str(Path("/workspace/other_dir")))

    assert watcher.dirty_repo_roots([repo_a, repo_b]) == set()


def test_dirty_repo_roots_matches_repo_root_itself() -> None:
    watcher = WorkspaceFileWatcher()
    repo_a = Path("/workspace/repo_a")

    watcher._on_directory_changed(str(repo_a))

    assert watcher.dirty_repo_roots([repo_a]) == {repo_a}


def test_dirty_paths_reset_after_changed_signal_fires() -> None:
    watcher = WorkspaceFileWatcher()
    repo_a = Path("/workspace/repo_a")
    received: list[None] = []
    watcher.changed.connect(lambda: received.append(None))

    watcher._on_directory_changed(str(repo_a / "src"))
    assert watcher.dirty_repo_roots([repo_a]) == {repo_a}

    watcher._on_debounce_timeout()

    assert len(received) == 1
    assert watcher.dirty_repo_roots([repo_a]) == set()


def test_dirty_paths_accumulate_across_multiple_changes_before_emit() -> None:
    watcher = WorkspaceFileWatcher()
    repo_a = Path("/workspace/repo_a")
    repo_b = Path("/workspace/repo_b")

    watcher._on_directory_changed(str(repo_a / "src"))
    watcher._on_directory_changed(str(repo_b / "lib"))

    assert watcher.dirty_repo_roots([repo_a, repo_b]) == {repo_a, repo_b}


def test_file_changed_marks_owning_repo_dirty() -> None:
    # directoryChanged never fires for an in-place edit to an already-
    # tracked file (no create/delete/rename); fileChanged is what closes
    # that gap, and it must feed the same dirty-path bookkeeping.
    watcher = WorkspaceFileWatcher()
    repo_a = Path("/workspace/repo_a")

    watcher._on_file_changed(str(repo_a / "src" / "already_changed.py"))

    assert watcher.dirty_repo_roots([repo_a]) == {repo_a}


def test_file_changed_signal_is_wired_to_the_underlying_watcher() -> None:
    watcher = WorkspaceFileWatcher()
    repo_a = Path("/workspace/repo_a")

    # Emit the real Qt signal (rather than calling the handler directly) to
    # prove fileChanged is actually connected, not just that the handler
    # works in isolation.
    watcher._watcher.fileChanged.emit(str(repo_a / "already_changed.py"))

    assert watcher.dirty_repo_roots([repo_a]) == {repo_a}


def test_set_watched_files_caps_at_max_and_marks_watcher_files(tmp_path: Path) -> None:
    files = []
    for i in range(5):
        f = tmp_path / f"f{i}.py"
        f.write_text("x")
        files.append(f)

    watcher = WorkspaceFileWatcher()
    watcher.set_watched_files(files)

    assert set(watcher._watcher.files()) == {str(f) for f in files}


def test_set_watched_files_truncates_beyond_cap(tmp_path: Path, monkeypatch) -> None:
    from local_changes_viewer.gui import workspace_watcher as ww

    monkeypatch.setattr(ww, "_MAX_WATCHED_FILES", 2)
    files = []
    for i in range(4):
        f = tmp_path / f"f{i}.py"
        f.write_text("x")
        files.append(f)

    watcher = WorkspaceFileWatcher()
    watcher.set_watched_files(files)

    assert len(watcher._watcher.files()) == 2


def test_stop_clears_watched_files_too() -> None:
    watcher = WorkspaceFileWatcher()
    real_file = Path(__file__)
    watcher.set_watched_files([real_file])
    assert watcher._watcher.files()

    watcher.stop()

    assert watcher._watcher.files() == []
