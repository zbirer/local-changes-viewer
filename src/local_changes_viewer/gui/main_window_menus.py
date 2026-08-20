from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence

from local_changes_viewer.gui.help_dialog import (
    show_actions_help,
    show_pull_requests_help,
    show_settings_help,
    show_toolbar_help,
)

if TYPE_CHECKING:
    from local_changes_viewer.gui.main_window import MainWindow


def build_menus(window: "MainWindow") -> None:
    actions_menu = window.menuBar().addMenu("Actions")

    open_action = QAction("Open Folder…", window)
    open_action.triggered.connect(window._on_open_folder)
    actions_menu.addAction(open_action)

    verify_changes_action = QAction("Verify Changes Against Git…", window)
    verify_changes_action.triggered.connect(window._on_verify_changes)
    actions_menu.addAction(verify_changes_action)

    view_menu = window.menuBar().addMenu("View")

    collapse_all_action = QAction("Collapse All", window)
    collapse_all_action.triggered.connect(window._tree_view.collapse_all)
    view_menu.addAction(collapse_all_action)

    expand_all_action = QAction("Expand All", window)
    expand_all_action.triggered.connect(window._tree_view.expand_all)
    view_menu.addAction(expand_all_action)

    expand_changed_repos_action = QAction("Expand Changed Repos", window)
    expand_changed_repos_action.triggered.connect(window._tree_view.expand_changed_repos)
    view_menu.addAction(expand_changed_repos_action)

    view_menu.addSeparator()

    expand_current_repo_action = QAction("Expand Current Repository", window)
    expand_current_repo_action.triggered.connect(window._tree_view.expand_current_repo)
    view_menu.addAction(expand_current_repo_action)

    collapse_current_repo_action = QAction("Collapse Current Repository", window)
    collapse_current_repo_action.triggered.connect(window._tree_view.collapse_current_repo)
    view_menu.addAction(collapse_current_repo_action)

    view_menu.addSeparator()

    open_pr_panel_view_action = QAction("Open PRs Panel", window)
    open_pr_panel_view_action.triggered.connect(window._on_open_pull_requests_panel)
    view_menu.addAction(open_pr_panel_view_action)

    view_menu.addSeparator()

    window._file_history_action = QAction("File History…", window)
    # Starts disabled: nothing is selected yet. This is the app's first
    # tree-selection-driven QAction enablement -- every other setEnabled
    # call (:600, 1206, 1216, 1644, 1647) keys off GitHub-connection or
    # worktree-running state instead. _on_folder_scope_changed flips it
    # on the moment scope_changed reports anything selected.
    window._file_history_action.setEnabled(False)
    # Cmd/Ctrl+F on the folder tree opens File History for the selected
    # scope. Ctrl+F is NOT free at window level -- SideBySideView binds it to
    # the inline find bar (side_by_side_view.py:311), scoped to the right
    # diff pane with WidgetWithChildrenShortcut. Scoping this one the same
    # way, to the tree, is what keeps both alive: the two widgets can never
    # hold focus at once, so Qt never sees an ambiguous activation and the
    # find bar keeps working untouched. addAction is what gives the action a
    # focus widget to be scoped against; the tree uses CustomContextMenu, so
    # this does not add an entry to its right-click menu. The View menu still
    # displays "Ctrl+F" beside the item, which is the discoverability the
    # bare QShortcut alternative would have lost.
    window._file_history_action.setShortcut(QKeySequence("Ctrl+F"))
    window._file_history_action.setShortcutContext(
        Qt.ShortcutContext.WidgetWithChildrenShortcut
    )
    window._tree_view.addAction(window._file_history_action)
    window._file_history_action.triggered.connect(window._on_file_history_from_menu)
    view_menu.addAction(window._file_history_action)

    view_menu.addSeparator()

    increase_font_action = QAction("Increase Font Size", window)
    increase_font_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
    increase_font_action.triggered.connect(window._diff_view.increase_font_size)
    view_menu.addAction(increase_font_action)

    decrease_font_action = QAction("Decrease Font Size", window)
    decrease_font_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
    decrease_font_action.triggered.connect(window._diff_view.decrease_font_size)
    view_menu.addAction(decrease_font_action)

    view_menu.addSeparator()

    settings_dialog_action = QAction("Settings…", window)
    # On macOS, Qt sniffs an action's text and moves anything looking like
    # "settings"/"preferences"/"options" into the application menu. NoRole
    # keeps this item where the user is looking for it: the View menu.
    settings_dialog_action.setMenuRole(QAction.MenuRole.NoRole)
    settings_dialog_action.triggered.connect(window._on_open_settings_dialog)
    view_menu.addAction(settings_dialog_action)
    window._settings_dialog_action = settings_dialog_action

    view_menu.addSeparator()

    window._profile_menu = view_menu.addMenu("Profile")
    window._profile_action_group = QActionGroup(window)
    window._profile_action_group.setExclusive(True)
    window._rebuild_profile_menu()

    settings_menu = window.menuBar().addMenu("Settings")

    window._include_ignored_action = QAction("Show ignored files", window, checkable=True)
    window._include_ignored_action.toggled.connect(window._on_include_ignored_toggled)
    settings_menu.addAction(window._include_ignored_action)

    window._include_unpushed_commits_action = QAction(
        "Show committed but not pushed files", window, checkable=True
    )
    window._include_unpushed_commits_action.toggled.connect(
        window._on_include_unpushed_commits_toggled
    )
    settings_menu.addAction(window._include_unpushed_commits_action)

    window._ignore_whitespace_action = QAction("Ignore whitespace", window, checkable=True)
    window._ignore_whitespace_action.toggled.connect(window._on_ignore_whitespace_toggled)
    settings_menu.addAction(window._ignore_whitespace_action)

    window._ignore_md_action = QAction("Ignore MD files", window, checkable=True)
    window._ignore_md_action.toggled.connect(window._on_display_filter_toggled)
    settings_menu.addAction(window._ignore_md_action)

    window._hide_empty_repos_action = QAction(
        "Hide repos without changes", window, checkable=True
    )
    window._hide_empty_repos_action.toggled.connect(window._on_display_filter_toggled)
    settings_menu.addAction(window._hide_empty_repos_action)

    window._sync_scroll_action = QAction(
        "Sync side-by-side scroll", window, checkable=True
    )
    window._sync_scroll_action.toggled.connect(window._on_sync_scroll_toggled)
    settings_menu.addAction(window._sync_scroll_action)

    window._always_reload_diff_action = QAction(
        "Always reload fresh diff", window, checkable=True
    )
    window._always_reload_diff_action.toggled.connect(window._on_always_reload_diff_toggled)
    settings_menu.addAction(window._always_reload_diff_action)

    auto_refresh_action = QAction("Auto Refresh…", window)
    auto_refresh_action.triggered.connect(window._on_configure_auto_refresh)
    settings_menu.addAction(auto_refresh_action)

    window._use_file_watcher_action = QAction(
        "Watch for File Changes", window, checkable=True
    )
    window._use_file_watcher_action.toggled.connect(window._on_use_file_watcher_toggled)
    settings_menu.addAction(window._use_file_watcher_action)

    log_level_action = QAction("Log Level…", window)
    log_level_action.triggered.connect(window._on_configure_log_level)
    settings_menu.addAction(log_level_action)

    tooltip_font_size_action = QAction("Tooltip Font Size…", window)
    tooltip_font_size_action.triggered.connect(window._on_configure_tooltip_font_size)
    settings_menu.addAction(tooltip_font_size_action)

    manage_folder_filters_action = QAction("Filtered Folders…", window)
    manage_folder_filters_action.triggered.connect(window._on_manage_folder_filters)
    settings_menu.addAction(manage_folder_filters_action)

    manage_profiles_action = QAction("Profiles…", window)
    manage_profiles_action.triggered.connect(window._on_manage_profiles)
    settings_menu.addAction(manage_profiles_action)

    github_menu = window.menuBar().addMenu("GitHub")

    my_pull_requests_action = QAction("My Open Pull Requests…", window)
    my_pull_requests_action.triggered.connect(window._on_show_my_pull_requests)
    github_menu.addAction(my_pull_requests_action)

    open_pr_panel_action = QAction("Open PRs Panel", window)
    open_pr_panel_action.triggered.connect(window._on_open_pull_requests_panel)
    github_menu.addAction(open_pr_panel_action)

    github_menu.addSeparator()

    connect_github_action = QAction("Connect to GitHub…", window)
    connect_github_action.triggered.connect(window._on_connect_github)
    github_menu.addAction(connect_github_action)

    window._disconnect_github_action = QAction("Disconnect GitHub", window)
    window._disconnect_github_action.triggered.connect(window._on_disconnect_github)
    github_menu.addAction(window._disconnect_github_action)

    help_menu = window.menuBar().addMenu("Help")

    help_settings_action = QAction("Help on Settings", window)
    help_settings_action.triggered.connect(lambda: show_settings_help(window))
    help_menu.addAction(help_settings_action)

    help_actions_action = QAction("Help on Actions", window)
    help_actions_action.triggered.connect(lambda: show_actions_help(window))
    help_menu.addAction(help_actions_action)

    help_pr_action = QAction("Help on PR Panel / Dialog", window)
    help_pr_action.triggered.connect(lambda: show_pull_requests_help(window))
    help_menu.addAction(help_pr_action)

    help_toolbar_action = QAction("Help on Toolbar Buttons", window)
    help_toolbar_action.triggered.connect(lambda: show_toolbar_help(window))
    help_menu.addAction(help_toolbar_action)

    actions_menu.addSeparator()

    app_log_action = QAction("App Log", window)
    app_log_action.triggered.connect(window._on_copy_app_log)
    actions_menu.addAction(app_log_action)

    error_log_action = QAction("Error Log", window)
    error_log_action.triggered.connect(window._on_show_error_log)
    actions_menu.addAction(error_log_action)

    copy_diff_action = QAction("Copy Diff", window)
    copy_diff_action.triggered.connect(window._on_copy_diff)
    actions_menu.addAction(copy_diff_action)

    copy_path_action = QAction("Copy File Path", window)
    copy_path_action.triggered.connect(window._on_copy_file_path)
    actions_menu.addAction(copy_path_action)

    copy_name_action = QAction("Copy File Name", window)
    copy_name_action.triggered.connect(window._on_copy_file_name)
    actions_menu.addAction(copy_name_action)

    open_editor_action = QAction("Open in Default Editor", window)
    open_editor_action.triggered.connect(window._on_open_in_editor)
    actions_menu.addAction(open_editor_action)

    reveal_action = QAction("Reveal in Finder", window)
    reveal_action.triggered.connect(window._on_reveal_in_finder)
    actions_menu.addAction(reveal_action)

    actions_menu.addSeparator()

    refresh_action = QAction("Refresh", window)
    refresh_action.setShortcut(QKeySequence("Ctrl+R"))
    refresh_action.triggered.connect(window._on_refresh)
    actions_menu.addAction(refresh_action)

    toggle_time_filter_action = QAction("Toggle Last Commit Time Filter", window)
    toggle_time_filter_action.setShortcut(QKeySequence("Ctrl+D"))
    toggle_time_filter_action.triggered.connect(window._on_toggle_time_filter)
    actions_menu.addAction(toggle_time_filter_action)
