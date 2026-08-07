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
