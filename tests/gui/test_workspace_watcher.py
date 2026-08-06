from pathlib import Path

from local_changes_viewer.gui.workspace_watcher import collect_watch_paths


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
