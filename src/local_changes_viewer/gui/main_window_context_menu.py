from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QMenu

from local_changes_viewer.gui.main_window_helpers import find_repository
from local_changes_viewer.gui.workspace_tree.tree_model import (
    FILE_CHANGE_ROLE,
    FOLDER_PATH_ROLE,
    NODE_KEY_ROLE,
)

if TYPE_CHECKING:
    from local_changes_viewer.gui.main_window import MainWindow


def show_tree_context_menu(window: "MainWindow", pos) -> None:
    index = window._tree_view.indexAt(pos)
    if not index.isValid():
        return

    if index.data(FILE_CHANGE_ROLE) is not None:
        window._tree_view.setCurrentIndex(index)
        menu = QMenu(window._tree_view)
        menu.addAction("Copy Path", window._on_copy_file_path)
        menu.addAction("Copy Name", window._on_copy_file_name)
        menu.addAction("Refresh Diff", window._on_refresh_diff)
        menu.addAction("Create patch", window._on_create_patch_for_file)
        menu.addAction("File History…", window._on_file_history_for_file)
        menu.addSeparator()
        menu.addAction("Filter Out This File", window._on_filter_out_file)
        menu.exec(window._tree_view.viewport().mapToGlobal(pos))
        return

    folder_path = index.data(FOLDER_PATH_ROLE)
    if folder_path is not None:
        is_repo_root = index.data(NODE_KEY_ROLE) == folder_path
        menu = QMenu(window._tree_view)
        menu.addAction("Copy Name", lambda: window._on_copy_folder_name(folder_path))
        menu.addAction("Copy Path", lambda: window._on_copy_folder_path(folder_path))
        menu.addAction(
            "Filter Out This Folder", lambda: window._on_filter_out_folder(folder_path)
        )
        menu.addAction(
            "Create patch", lambda: window._on_create_patch_for_folder(folder_path)
        )
        # Unlike "Show Log" below, which only ever runs on a repo root,
        # File History is deliberately offered on any folder -- so this
        # sits outside the `if is_repo_root:` block below, not inside it.
        menu.addAction(
            "File History…", lambda: window._on_file_history(Path(folder_path))
        )
        menu.addSeparator()
        menu.addAction(
            "Expand All", lambda: window._tree_view.expand_index_recursive(index)
        )
        menu.addAction(
            "Collapse All", lambda: window._tree_view.collapse_index_recursive(index)
        )
        if is_repo_root:
            menu.addSeparator()
            menu.addAction(
                "Refresh Repo", lambda: window._on_refresh_repo(Path(folder_path))
            )
            menu.addAction(
                "Show Log", lambda: window._on_show_log(Path(folder_path))
            )
            menu.addAction(
                "Show stashes...", lambda: window._on_show_stashes_for_repo(folder_path)
            )
            menu.addAction(
                "List Worktrees", lambda: window._on_list_worktrees(Path(folder_path))
            )
            menu.addAction(
                "Apply patch...", lambda: window._on_apply_patch_for_repo(folder_path)
            )
            repo = find_repository(window._workspace, Path(folder_path))
            if repo is not None and repo.logical_parent_path is not None:
                menu.addSeparator()
                running = Path(folder_path) in window._worktree_terminal_windows
                menu.addAction(
                    "Start", lambda: window._on_start_worktree(Path(folder_path))
                ).setEnabled(not running)
                menu.addAction(
                    "Stop", lambda: window._on_stop_worktree(Path(folder_path))
                ).setEnabled(running)
        if not index.parent().isValid():
            repo_name = Path(folder_path).name
            menu.addSeparator()
            add_profile_submenu(window, menu, repo_name)
        menu.exec(window._tree_view.viewport().mapToGlobal(pos))


def add_profile_submenu(window: "MainWindow", menu: QMenu, repo_name: str) -> None:
    submenu = menu.addMenu("Add to Profile")
    for profile in window._profiles:
        action = submenu.addAction(profile.name)
        action.setCheckable(True)
        action.setChecked(repo_name in profile.repo_names)
        action.toggled.connect(
            lambda checked, name=profile.name: (
                window._on_add_repo_to_profile(repo_name, name)
                if checked
                else window._on_remove_repo_from_profile(repo_name, name)
            )
        )
    if window._profiles:
        submenu.addSeparator()
    submenu.addAction("New Profile…", lambda: window._on_new_profile_with_repo(repo_name))
