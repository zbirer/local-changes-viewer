import threading
import time
from collections.abc import Callable
from pathlib import Path

import git

from PySide6.QtCore import QModelIndex, QProcess, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QDesktopServices,
    QGuiApplication,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterMode, FolderFilterRule
from local_changes_viewer.core.domain.profile import Profile
from local_changes_viewer.core.domain.pull_request import PullRequestInfo
from local_changes_viewer.core.domain.repository import Repository
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.infra.git_repo_adapter import GitRepoAdapter
from local_changes_viewer.core.infra.github_client import (
    GitHubClient,
    GitHubError,
    parse_github_owner_repo,
)
from local_changes_viewer.core.services.diff_formatting import format_unified_diff
from local_changes_viewer.core.services.file_info import detect_encoding, detect_line_ending
from local_changes_viewer.core.services.patch_service import PatchService
from local_changes_viewer.core.services.workspace_cache import load_workspace, save_workspace
from local_changes_viewer.core.services.workspace_filter import filter_workspace
from local_changes_viewer.core.services.workspace_scanner_service import (
    WorkspaceScannerService,
)
from local_changes_viewer.core.services.worktree_terminal_service import (
    WorktreeTerminalError,
    start_worktree_process,
    stop_worktree_process,
)
from local_changes_viewer.gui import applog, github_auth
from local_changes_viewer.gui.commit_log_dialog import CommitLogDialog
from local_changes_viewer.gui.diff_view.diff_view_widget import DiffViewWidget
from local_changes_viewer.gui.error_log_dialog import ErrorLogDialog
from local_changes_viewer.gui.folder_filter_dialog import FolderFilterDialog
from local_changes_viewer.gui.github_connect_dialog import GitHubConnectDialog
from local_changes_viewer.gui.help_dialog import (
    HelpDialog,
    show_actions_help,
    show_pull_requests_help,
    show_settings_help,
    show_toolbar_help,
)
from local_changes_viewer.gui.my_pull_requests_dialog import MyPullRequestsDialog
from local_changes_viewer.gui.patch_file_selection_dialog import PatchFileSelectionDialog
from local_changes_viewer.gui.patch_text_input_dialog import PatchTextInputDialog
from local_changes_viewer.gui.profile_dialog import ProfileDialog
from local_changes_viewer.gui.pull_requests_panel import PullRequestsPanel
from local_changes_viewer.gui.pull_request_info_dialog import PullRequestInfoDialog
from local_changes_viewer.gui.pull_request_issues_dialog import PullRequestIssuesDialog
from local_changes_viewer.gui.settings import AppSettings
from local_changes_viewer.gui.settings_dialog import SettingsDialog
from local_changes_viewer.gui.stashes_dialog import StashesDialog
from local_changes_viewer.gui.workers.diff_worker import DiffWorker
from local_changes_viewer.gui.workers.my_pull_requests_worker import MyPullRequestsWorker
from local_changes_viewer.gui.workers.pull_request_details_worker import PullRequestDetailsWorker
from local_changes_viewer.gui.workers.pull_request_refresh_worker import PullRequestRefreshWorker
from local_changes_viewer.gui.workers.pull_request_threads_worker import PullRequestThreadsWorker
from local_changes_viewer.gui.workers.repo_refresh_worker import RepoRefreshWorker
from local_changes_viewer.gui.workers.scan_worker import ScanWorker
from local_changes_viewer.gui.workers.watch_paths_worker import WatchPathsWorker
from local_changes_viewer.gui.workers.worker_keeper import start_worker
from local_changes_viewer.gui.worktrees_dialog import WorktreesDialog
from local_changes_viewer.gui.workspace_watcher import WorkspaceFileWatcher
from local_changes_viewer.gui.workspace_tree.aggregate_list import AggregateChangeList
from local_changes_viewer.gui.workspace_tree.tree_model import (
    FILE_CHANGE_ROLE,
    FOLDER_PATH_ROLE,
    NODE_KEY_ROLE,
    REPO_PATH_ROLE,
)
from local_changes_viewer.gui.workspace_tree.tree_view import RepoTreeView

# A busy workspace (many repos + a fast file watcher) can fire an auto-refresh
# scan every couple of seconds; if the previous scan just finished, skip this
# one rather than piling another full 27-repo git+GitHub scan on top of it.
_MIN_AUTO_REFRESH_INTERVAL_SECONDS = 5.0

# _update_file_info_label runs on the GUI thread on every file selection, and
# only needs enough bytes to sniff an encoding/line-ending -- reading a whole
# multi-GB file into memory there would freeze the UI and could exhaust
# memory, so the read is capped regardless of the file's actual size.
_FILE_INFO_SNIFF_BYTES = 1 * 1024 * 1024


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        # Used to log how long it takes from process start until the folder
        # tree is first painted (from cache, when a matching cache exists).
        self._app_started_at = time.monotonic()
        self.setWindowTitle("local-changes-viewer")
        self.resize(1200, 800)

        self._settings = AppSettings()
        applog.set_level(applog.level_from_name(self._settings.log_level()))
        self._root_folder: str | None = None
        self._workspace: Workspace | None = None
        self._folder_filter_rules: list[FolderFilterRule] = self._settings.folder_filter_rules()
        self._profiles: list[Profile] = self._settings.profiles()
        self._active_profile_name: str | None = self._settings.active_profile_name()
        self._selected_change: FileChange | None = None
        self._selected_repo_path: Path | None = None
        self._patch_service = PatchService()
        # Guards _restore_previous_selection() (Bug 4) against re-entering
        # _on_file_selected: programmatically restoring the tree/list's
        # current index re-fires file_selected via Qt's own
        # currentChanged, which would otherwise re-run the same
        # discard-edits confirmation for the file being restored.
        self._restoring_selection = False
        self._thread_pool = QThreadPool.globalInstance()
        # Guards _on_refresh_repo against a double-click (or a context-menu
        # click plus a row-button click) firing two concurrent
        # RepoRefreshWorker runs for the same repo -- the worker itself has
        # no such guard.
        self._refreshing_repo_paths: set[Path] = set()
        # Maps a running worktree's path to the AppleScript id of the Terminal.app
        # window "Start" opened for it, so "Stop" can signal that exact run later.
        self._worktree_terminal_windows: dict[Path, int] = {}
        self._shutdown_requested = threading.Event()
        self._scan_refresh_timer = QTimer(self)
        self._scan_refresh_timer.setInterval(150)
        # Must update in place (preserve_tree=True), never clear: this timer
        # fires ~100x during a single scan, and clearing/rebuilding the tree
        # on every tick destroys expansion/scroll state mid-scan (and, worse,
        # briefly renders zero rows -- see _on_scan_refresh_tick).
        self._scan_refresh_timer.timeout.connect(self._on_scan_refresh_tick)
        self._incremental_scan = False
        self._scan_in_progress = False
        # Bumped every time _start_scan actually launches a worker. Each
        # worker's result handlers are bound to the generation that was
        # current when it started (see _start_scan), so a scan superseded by
        # a later one (folder switched, or a user-initiated toggle/refresh
        # fired while a scan was still in flight) has its results silently
        # dropped instead of clobbering the newer scan's workspace or the
        # on-disk cache with stale data.
        self._scan_generation = 0
        # True only while __init__ is replaying persisted settings via
        # setChecked(); handlers that would kick off a scan or a tree rebuild
        # check this and bail out, so restoring N toggle settings can never
        # start N redundant scans on top of the one _restore_last_folder()
        # already started (see _restore_window_state / D1).
        self._restoring_settings = False
        self._scan_started_at = 0.0
        self._current_scan_label = "startup"
        self._startup_scan_pending = True
        self._last_scan_finished_at = 0.0
        self._showing_stale_cache = False
        self._auto_refresh_minutes = 0
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._on_auto_refresh_timeout)
        # applog.log (and therefore an ERROR entry) can be called from worker
        # threads (QRunnable.run), but the status-bar indicator is a QWidget
        # and must only ever be touched from the GUI thread -- so rather than
        # wiring a callback from applog straight into MainWindow (which would
        # fire on whichever thread logged the error), a dedicated GUI-thread
        # timer polls applog.error_count() instead. Neither existing timer
        # fits: _scan_refresh_timer only runs while a scan is in flight, and
        # _auto_refresh_timer can be off or many minutes long depending on
        # settings -- either would leave an off-thread error unreported
        # indefinitely. A short interval keeps "how stale can the indicator
        # be" bounded without being a measurable GUI-thread cost.
        self._error_indicator_timer = QTimer(self)
        self._error_indicator_timer.setInterval(2000)
        self._error_indicator_timer.timeout.connect(self._refresh_error_indicator)
        self._error_indicator_timer.start()
        self._file_watcher = WorkspaceFileWatcher(self)
        self._file_watcher.changed.connect(self._on_file_watcher_changed)
        # Kept alive across scans so WorkspaceScannerService's internal repo/PR
        # cache can actually skip work on unchanged repos between refreshes.
        self._scanner_service = WorkspaceScannerService()
        self._my_pull_requests_dialog: MyPullRequestsDialog | None = None
        self._pr_panel = PullRequestsPanel()
        self._pr_panel.hide()
        self._pr_panel.refresh_requested.connect(self._on_my_pull_requests_refresh_requested)
        self._pr_panel.pull_request_refresh_requested.connect(
            self._on_pull_request_refresh_requested
        )
        self._pr_panel.pull_request_info_requested.connect(self._on_pull_request_info_requested)
        self._pr_panel.pull_request_issues_requested.connect(
            self._on_pull_request_issues_requested
        )

        self._tree_view = RepoTreeView(self._settings)
        self._tree_view.file_selected.connect(self._on_file_selected)
        self._tree_view.refresh_repo_requested.connect(self._on_refresh_repo)
        self._tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree_view.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._filter_box = QLineEdit()
        self._filter_box.setPlaceholderText("Filter by path…")
        self._filter_box.textChanged.connect(self._tree_view.set_filter_text)

        # Route through RepoTreeView.collapse_all()/expand_all() (same calls
        # the View menu's Collapse All/Expand All actions use) rather than
        # QTreeView.collapseAll()/expandAll() directly -- those two methods
        # also persist the resulting collapsed-node-key set via AppSettings,
        # so a click here doesn't get silently undone the next time the tree
        # rebuilds and _restore_collapsed_state() replays stale settings.
        # "All Changes" is a flat QListWidget with no folder concept, so
        # these only ever act on the Folder Tree tab's tree, regardless of
        # which tab is currently active.
        #
        # Plain up/down arrows (SP_ArrowUp/SP_ArrowDown), not folder glyphs:
        # a closed-vs-open folder icon pair reads as "folder" at 16px, not
        # "collapse" vs "expand" -- direction is what actually carries the
        # meaning here, and title-bar shade/unshade icons are not guaranteed
        # to stay arrow-shaped across every native style the way plain arrows
        # are.
        button_side = self._filter_box.sizeHint().height()

        # "Hide empty worktrees" is the worktree-specific counterpart to the
        # "Hide repos without changes" setting (F35): F35 deliberately
        # exempts every worktree from its own rule (see f61bf6b/1c278f2 --
        # a worktree is navigational structure the user jumps between
        # branches with, not something to gate behind "has changes" the way
        # a stale regular repo is). That exemption is right for F35, but it
        # leaves no way to declutter a workspace with many long-lived,
        # mostly-clean worktrees -- so this checkbox is a second, independent
        # switch that targets only worktrees, off by default so a user who
        # never touches it sees no change from today's behavior.
        self._hide_changeless_worktrees_checkbox = QCheckBox("Hide empty worktrees")
        self._hide_changeless_worktrees_checkbox.setToolTip(
            "Checked: worktrees with no changed files are hidden from the "
            "folder tree.\nUnchecked (default): every worktree is always "
            'shown, regardless of changes -- matching "List Worktrees".'
        )
        self._hide_changeless_worktrees_checkbox.setFixedHeight(button_side)
        self._hide_changeless_worktrees_checkbox.toggled.connect(
            self._on_display_filter_toggled
        )

        self._collapse_all_folders_button = QToolButton()
        self._collapse_all_folders_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp)
        )
        self._collapse_all_folders_button.setToolTip("Collapse all folders")
        self._collapse_all_folders_button.setAutoRaise(True)
        self._collapse_all_folders_button.setFixedSize(button_side, button_side)
        self._collapse_all_folders_button.clicked.connect(self._tree_view.collapse_all)

        self._expand_all_folders_button = QToolButton()
        self._expand_all_folders_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowDown)
        )
        self._expand_all_folders_button.setToolTip("Expand all folders")
        self._expand_all_folders_button.setAutoRaise(True)
        self._expand_all_folders_button.setFixedSize(button_side, button_side)
        self._expand_all_folders_button.clicked.connect(self._tree_view.expand_all)
        self._time_filter_minutes = 0
        self._diff_view = DiffViewWidget()
        self._diff_view.refresh_requested.connect(self._on_refresh)
        self._diff_view.time_filter_minutes_changed.connect(self._on_time_filter_changed)
        self._diff_view.file_saved.connect(self._on_file_saved)
        self._diff_view.pull_requests_requested.connect(self._on_show_my_pull_requests)

        self._aggregate_list = AggregateChangeList()
        self._aggregate_list.file_selected.connect(self._on_file_selected)
        self._tree_view.scope_changed.connect(self._aggregate_list.set_scope)

        left_tabs = QTabWidget()
        left_tabs.addTab(self._tree_view, "Folder Tree")
        left_tabs.addTab(self._aggregate_list, "All Changes")

        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(6, 6, 6, 0)

        filter_row = QWidget()
        filter_row_layout = QHBoxLayout(filter_row)
        filter_row_layout.setContentsMargins(0, 0, 0, 0)
        filter_row_layout.setSpacing(2)
        filter_row_layout.addWidget(self._filter_box)
        filter_row_layout.addWidget(self._hide_changeless_worktrees_checkbox)
        filter_row_layout.addWidget(self._collapse_all_folders_button)
        filter_row_layout.addWidget(self._expand_all_folders_button)
        tree_layout.addWidget(filter_row)

        self._left_vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self._left_vertical_splitter.addWidget(left_tabs)
        self._left_vertical_splitter.addWidget(self._pr_panel)
        self._left_vertical_splitter.setStretchFactor(0, 1)
        self._left_vertical_splitter.setStretchFactor(1, 1)
        tree_layout.addWidget(self._left_vertical_splitter)

        self._splitter = QSplitter()
        self._splitter.addWidget(tree_panel)
        self._splitter.addWidget(self._diff_view)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 2)
        self.setCentralWidget(self._splitter)

        actions_menu = self.menuBar().addMenu("Actions")

        open_action = QAction("Open Folder…", self)
        open_action.triggered.connect(self._on_open_folder)
        actions_menu.addAction(open_action)

        verify_changes_action = QAction("Verify Changes Against Git…", self)
        verify_changes_action.triggered.connect(self._on_verify_changes)
        actions_menu.addAction(verify_changes_action)

        view_menu = self.menuBar().addMenu("View")

        collapse_all_action = QAction("Collapse All", self)
        collapse_all_action.triggered.connect(self._tree_view.collapse_all)
        view_menu.addAction(collapse_all_action)

        expand_all_action = QAction("Expand All", self)
        expand_all_action.triggered.connect(self._tree_view.expand_all)
        view_menu.addAction(expand_all_action)

        expand_changed_repos_action = QAction("Expand Changed Repos", self)
        expand_changed_repos_action.triggered.connect(self._tree_view.expand_changed_repos)
        view_menu.addAction(expand_changed_repos_action)

        view_menu.addSeparator()

        expand_current_repo_action = QAction("Expand Current Repository", self)
        expand_current_repo_action.triggered.connect(self._tree_view.expand_current_repo)
        view_menu.addAction(expand_current_repo_action)

        collapse_current_repo_action = QAction("Collapse Current Repository", self)
        collapse_current_repo_action.triggered.connect(self._tree_view.collapse_current_repo)
        view_menu.addAction(collapse_current_repo_action)

        view_menu.addSeparator()

        open_pr_panel_view_action = QAction("Open PRs Panel", self)
        open_pr_panel_view_action.triggered.connect(self._on_open_pull_requests_panel)
        view_menu.addAction(open_pr_panel_view_action)

        view_menu.addSeparator()

        increase_font_action = QAction("Increase Font Size", self)
        increase_font_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        increase_font_action.triggered.connect(self._diff_view.increase_font_size)
        view_menu.addAction(increase_font_action)

        decrease_font_action = QAction("Decrease Font Size", self)
        decrease_font_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        decrease_font_action.triggered.connect(self._diff_view.decrease_font_size)
        view_menu.addAction(decrease_font_action)

        view_menu.addSeparator()

        settings_dialog_action = QAction("Settings…", self)
        # On macOS, Qt sniffs an action's text and moves anything looking like
        # "settings"/"preferences"/"options" into the application menu. NoRole
        # keeps this item where the user is looking for it: the View menu.
        settings_dialog_action.setMenuRole(QAction.MenuRole.NoRole)
        settings_dialog_action.triggered.connect(self._on_open_settings_dialog)
        view_menu.addAction(settings_dialog_action)
        self._settings_dialog_action = settings_dialog_action

        view_menu.addSeparator()

        self._profile_menu = view_menu.addMenu("Profile")
        self._profile_action_group = QActionGroup(self)
        self._profile_action_group.setExclusive(True)
        self._rebuild_profile_menu()

        settings_menu = self.menuBar().addMenu("Settings")

        self._include_ignored_action = QAction("Show ignored files", self, checkable=True)
        self._include_ignored_action.toggled.connect(self._on_include_ignored_toggled)
        settings_menu.addAction(self._include_ignored_action)

        self._include_unpushed_commits_action = QAction(
            "Show committed but not pushed files", self, checkable=True
        )
        self._include_unpushed_commits_action.toggled.connect(
            self._on_include_unpushed_commits_toggled
        )
        settings_menu.addAction(self._include_unpushed_commits_action)

        self._ignore_whitespace_action = QAction("Ignore whitespace", self, checkable=True)
        self._ignore_whitespace_action.toggled.connect(self._on_ignore_whitespace_toggled)
        settings_menu.addAction(self._ignore_whitespace_action)

        self._ignore_md_action = QAction("Ignore MD files", self, checkable=True)
        self._ignore_md_action.toggled.connect(self._on_display_filter_toggled)
        settings_menu.addAction(self._ignore_md_action)

        self._hide_empty_repos_action = QAction(
            "Hide repos without changes", self, checkable=True
        )
        self._hide_empty_repos_action.toggled.connect(self._on_display_filter_toggled)
        settings_menu.addAction(self._hide_empty_repos_action)

        self._sync_scroll_action = QAction(
            "Sync side-by-side scroll", self, checkable=True
        )
        self._sync_scroll_action.toggled.connect(self._on_sync_scroll_toggled)
        settings_menu.addAction(self._sync_scroll_action)

        self._always_reload_diff_action = QAction(
            "Always reload fresh diff", self, checkable=True
        )
        self._always_reload_diff_action.toggled.connect(self._on_always_reload_diff_toggled)
        settings_menu.addAction(self._always_reload_diff_action)

        auto_refresh_action = QAction("Auto Refresh…", self)
        auto_refresh_action.triggered.connect(self._on_configure_auto_refresh)
        settings_menu.addAction(auto_refresh_action)

        self._use_file_watcher_action = QAction(
            "Watch for File Changes", self, checkable=True
        )
        self._use_file_watcher_action.toggled.connect(self._on_use_file_watcher_toggled)
        settings_menu.addAction(self._use_file_watcher_action)

        log_level_action = QAction("Log Level…", self)
        log_level_action.triggered.connect(self._on_configure_log_level)
        settings_menu.addAction(log_level_action)

        tooltip_font_size_action = QAction("Tooltip Font Size…", self)
        tooltip_font_size_action.triggered.connect(self._on_configure_tooltip_font_size)
        settings_menu.addAction(tooltip_font_size_action)

        manage_folder_filters_action = QAction("Filtered Folders…", self)
        manage_folder_filters_action.triggered.connect(self._on_manage_folder_filters)
        settings_menu.addAction(manage_folder_filters_action)

        manage_profiles_action = QAction("Profiles…", self)
        manage_profiles_action.triggered.connect(self._on_manage_profiles)
        settings_menu.addAction(manage_profiles_action)

        github_menu = self.menuBar().addMenu("GitHub")

        my_pull_requests_action = QAction("My Open Pull Requests…", self)
        my_pull_requests_action.triggered.connect(self._on_show_my_pull_requests)
        github_menu.addAction(my_pull_requests_action)

        open_pr_panel_action = QAction("Open PRs Panel", self)
        open_pr_panel_action.triggered.connect(self._on_open_pull_requests_panel)
        github_menu.addAction(open_pr_panel_action)

        github_menu.addSeparator()

        connect_github_action = QAction("Connect to GitHub…", self)
        connect_github_action.triggered.connect(self._on_connect_github)
        github_menu.addAction(connect_github_action)

        self._disconnect_github_action = QAction("Disconnect GitHub", self)
        self._disconnect_github_action.triggered.connect(self._on_disconnect_github)
        github_menu.addAction(self._disconnect_github_action)

        help_menu = self.menuBar().addMenu("Help")

        help_settings_action = QAction("Help on Settings", self)
        help_settings_action.triggered.connect(lambda: show_settings_help(self))
        help_menu.addAction(help_settings_action)

        help_actions_action = QAction("Help on Actions", self)
        help_actions_action.triggered.connect(lambda: show_actions_help(self))
        help_menu.addAction(help_actions_action)

        help_pr_action = QAction("Help on PR Panel / Dialog", self)
        help_pr_action.triggered.connect(lambda: show_pull_requests_help(self))
        help_menu.addAction(help_pr_action)

        help_toolbar_action = QAction("Help on Toolbar Buttons", self)
        help_toolbar_action.triggered.connect(lambda: show_toolbar_help(self))
        help_menu.addAction(help_toolbar_action)

        actions_menu.addSeparator()

        app_log_action = QAction("App Log", self)
        app_log_action.triggered.connect(self._on_copy_app_log)
        actions_menu.addAction(app_log_action)

        error_log_action = QAction("Error Log", self)
        error_log_action.triggered.connect(self._on_show_error_log)
        actions_menu.addAction(error_log_action)

        copy_diff_action = QAction("Copy Diff", self)
        copy_diff_action.triggered.connect(self._on_copy_diff)
        actions_menu.addAction(copy_diff_action)

        copy_path_action = QAction("Copy File Path", self)
        copy_path_action.triggered.connect(self._on_copy_file_path)
        actions_menu.addAction(copy_path_action)

        copy_name_action = QAction("Copy File Name", self)
        copy_name_action.triggered.connect(self._on_copy_file_name)
        actions_menu.addAction(copy_name_action)

        open_editor_action = QAction("Open in Default Editor", self)
        open_editor_action.triggered.connect(self._on_open_in_editor)
        actions_menu.addAction(open_editor_action)

        reveal_action = QAction("Reveal in Finder", self)
        reveal_action.triggered.connect(self._on_reveal_in_finder)
        actions_menu.addAction(reveal_action)

        actions_menu.addSeparator()

        refresh_action = QAction("Refresh", self)
        refresh_action.setShortcut(QKeySequence("Ctrl+R"))
        refresh_action.triggered.connect(self._on_refresh)
        actions_menu.addAction(refresh_action)

        toggle_time_filter_action = QAction("Toggle Last Commit Time Filter", self)
        toggle_time_filter_action.setShortcut(QKeySequence("Ctrl+D"))
        toggle_time_filter_action.triggered.connect(self._on_toggle_time_filter)
        actions_menu.addAction(toggle_time_filter_action)

        self._folder_status_label = QLabel("No folder open")
        self.statusBar().addPermanentWidget(self._folder_status_label)

        self._summary_label = QLabel("")
        self.statusBar().addPermanentWidget(self._summary_label)

        self._file_info_label = QLabel("")
        self.statusBar().addPermanentWidget(self._file_info_label)

        self._status_extra_label = QLabel("")
        self.statusBar().addPermanentWidget(self._status_extra_label)

        # A persistent, always-visible companion to the transient 5000ms
        # status-bar toasts above -- those vanish whether or not anyone saw
        # them, so this stays up (and clickable) until the error store is
        # cleared. A QLabel can't be clicked, hence QToolButton styled flat
        # to still read as a status-bar label at rest.
        self._error_indicator_button = QToolButton()
        self._error_indicator_button.setAutoRaise(True)
        self._error_indicator_button.setStyleSheet("color: #DC2626; font-weight: 600;")
        self._error_indicator_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._error_indicator_button.setVisible(False)
        self._error_indicator_button.clicked.connect(self._on_show_error_log)
        self.statusBar().addPermanentWidget(self._error_indicator_button)

        self._restore_last_folder()
        self._restoring_settings = True
        self._restore_window_state()
        self._restoring_settings = False
        self._auto_connect_github()

    def _restore_last_folder(self) -> None:
        last_folder = self._settings.last_root_folder()
        if last_folder:
            self._set_root_folder(last_folder)

    def _restore_window_state(self) -> None:
        geometry = self._settings.window_geometry()
        if geometry:
            self.restoreGeometry(geometry)

        sizes = self._settings.splitter_sizes()
        if sizes:
            self._splitter.setSizes(sizes)

        self._diff_view.set_side_by_side(self._settings.diff_view_mode() == "side_by_side")

        self._ignore_whitespace_action.setChecked(self._settings.ignore_whitespace())
        self._include_unpushed_commits_action.setChecked(
            self._settings.include_unpushed_commits()
        )
        self._ignore_md_action.setChecked(self._settings.ignore_md_files())
        self._hide_empty_repos_action.setChecked(self._settings.hide_repos_without_changes())
        self._hide_changeless_worktrees_checkbox.setChecked(
            self._settings.hide_changeless_worktrees()
        )
        self._sync_scroll_action.setChecked(self._settings.sync_side_by_side_scroll())
        self._diff_view.set_sync_scroll(self._sync_scroll_action.isChecked())
        self._always_reload_diff_action.setChecked(self._settings.always_reload_diff())
        self._apply_auto_refresh_interval(self._settings.auto_refresh_minutes())
        self._use_file_watcher_action.setChecked(self._settings.use_file_watcher())
        self._disconnect_github_action.setEnabled(self._settings.github_username() is not None)
        self._apply_tooltip_font_size(self._settings.tooltip_font_size())

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._diff_view.has_unsaved_edits():
            reply = QMessageBox.question(
                self,
                "Discard edits?",
                "You have unsaved edits to a file. Discard them and close?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._scan_refresh_timer.stop()
        self._auto_refresh_timer.stop()
        self._error_indicator_timer.stop()
        self._file_watcher.stop()
        self._shutdown_requested.set()
        self._thread_pool.clear()
        # Repo scans already mid-flight (e.g. a blocking GitHub API call) can't be
        # interrupted, so bound the wait rather than hang the app on quit.
        self._thread_pool.waitForDone(3000)
        self._settings.set_window_geometry(self.saveGeometry())
        self._settings.set_splitter_sizes(self._splitter.sizes())
        mode = "side_by_side" if self._diff_view.is_side_by_side() else "unified"
        self._settings.set_diff_view_mode(mode)
        self._settings.set_ignore_whitespace(self._ignore_whitespace_action.isChecked())
        self._settings.set_include_unpushed_commits(
            self._include_unpushed_commits_action.isChecked()
        )
        self._settings.set_ignore_md_files(self._ignore_md_action.isChecked())
        self._settings.set_hide_repos_without_changes(self._hide_empty_repos_action.isChecked())
        self._settings.set_hide_changeless_worktrees(
            self._hide_changeless_worktrees_checkbox.isChecked()
        )
        self._settings.set_sync_side_by_side_scroll(self._sync_scroll_action.isChecked())
        self._settings.set_always_reload_diff(self._always_reload_diff_action.isChecked())
        super().closeEvent(event)

    def _guard_worker_result(self, slot: Callable[..., None]) -> Callable[..., None]:
        """Wrap a background-worker signal's slot so a result that arrives
        after closeEvent has already set _shutdown_requested is dropped
        instead of calling back into this window.

        closeEvent's waitForDone(3000) is a bounded wait, not a guarantee --
        a worker doing a blocking git/GitHub call can still be running (and
        can still emit) well after that timeout, potentially after `self`'s
        underlying C++ object has been torn down by app shutdown. Reading
        `self._shutdown_requested` here, at connect time, rather than inside
        `guarded`, means the check itself never has to touch `self` once the
        worker later fires -- only `slot` (already a bound method holding
        its own reference) does, and only when we know shutdown hasn't
        started.
        """
        shutdown_requested = self._shutdown_requested

        def guarded(*args: object) -> None:
            if shutdown_requested.is_set():
                return
            slot(*args)

        return guarded

    def _on_open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open Folder")
        if folder:
            applog.log(f"Open Folder: {folder}", level=applog.LogLevel.INFO)
            self._set_root_folder(folder)

    def _on_include_ignored_toggled(self, checked: bool) -> None:
        applog.log(f"Show ignored files: {checked}", level=applog.LogLevel.INFO)
        if self._restoring_settings:
            return
        if self._root_folder:
            # Deliberately no _scan_in_progress guard here (unlike the
            # auto-refresh/file-watcher handlers below): this is a direct
            # user action, and silently swallowing the click would be worse
            # than starting a second scan. _start_scan bumps _scan_generation
            # on every call, so if a scan was already running, its eventual
            # result is superseded and dropped rather than raced against
            # this one (see _scan_generation).
            self._start_scan(self._root_folder)

    def _on_include_unpushed_commits_toggled(self, checked: bool) -> None:
        applog.log(f"Show committed but not pushed files: {checked}", level=applog.LogLevel.INFO)
        if self._restoring_settings:
            return
        if self._root_folder:
            # See _on_include_ignored_toggled: no in-progress guard by
            # design, superseding relies on _scan_generation instead.
            self._start_scan(self._root_folder)

    def _on_refresh(self) -> None:
        applog.log("Refresh", level=applog.LogLevel.INFO)
        if self._root_folder:
            # User-initiated refresh must never reuse a repo's cached git
            # state (Part 1 of the stale-cache fix) — an editor that writes a
            # tracked file in place never fires the file watcher's
            # directoryChanged signal, so relying on dirty_paths here would
            # leave a repo showing zero changes forever, even across repeated
            # explicit refreshes.
            # No _scan_in_progress guard either -- see
            # _on_include_ignored_toggled: superseding an in-flight scan is
            # _scan_generation's job, not this handler's.
            self._start_scan(
                self._root_folder, rebuild=self._workspace is None, force_full_rescan=True
            )

    def _on_verify_changes(self) -> None:
        """Self-check command: catches exactly the class of bug this session
        fixed (a repo's changes silently going stale) plus a render-side
        equivalent (a change the scanner kept but a display filter dropped
        for no accountable reason)."""
        applog.log("Verify changes against git", level=applog.LogLevel.INFO)
        if self._workspace is None:
            QMessageBox.information(self, "Verify Changes Against Git", "No workspace loaded yet.")
            return

        scan_results = self._scanner_service.verify_changes_against_git(
            self._workspace,
            include_ignored=self._include_ignored_action.isChecked(),
            include_unpushed_commits=self._include_unpushed_commits_action.isChecked(),
        )

        # Recompute what should survive the currently active display filters
        # (same call _refresh_display makes) and compare it against what the
        # tree actually rendered, so a render-side loss is caught too, not
        # just a scan-side one.
        expected_workspace = filter_workspace(
            self._workspace,
            ignore_md_files=self._ignore_md_action.isChecked(),
            hide_repos_without_changes=self._hide_empty_repos_action.isChecked(),
            hide_changeless_worktrees=self._hide_changeless_worktrees_checkbox.isChecked(),
            folder_filter_rules=self._folder_filter_rules,
            max_age_minutes=self._time_filter_minutes,
            profile=self._active_profile(),
        )
        shown_paths_by_repo: dict[Path, set[Path]] = {}
        for repo_path, change in self._tree_view.displayed_file_changes():
            shown_paths_by_repo.setdefault(repo_path, set()).add(change.path)

        render_loss_lines: list[str] = []
        for repo in expected_workspace.repositories:
            shown_paths = shown_paths_by_repo.get(repo.path, set())
            for change in repo.changes:
                if change.path in shown_paths:
                    continue
                reason = self._explain_missing_from_tree(repo, change)
                render_loss_lines.append(
                    f"<b>{repo.name}</b>: {change.path} is missing from the tree — "
                    f"{reason or '<b>unexplained</b>'}"
                )

        scan_discrepancies = [r for r in scan_results if not r.is_consistent]
        total_repos = len(self._workspace.repositories)
        total_files = sum(len(r.changes) for r in self._workspace.repositories)

        if not scan_discrepancies and not render_loss_lines:
            html = f"<p>{total_repos} repos, {total_files} files, all consistent.</p>"
        else:
            items: list[str] = []
            for result in scan_discrepancies:
                if result.error is not None:
                    items.append(
                        f"<li><b>{result.repo_name}</b>: failed to check against "
                        f"git — {result.error}</li>"
                    )
                    continue
                for path in result.missing_from_app:
                    items.append(
                        f"<li><b>{result.repo_name}</b>: git reports <code>{path}</code> as "
                        "changed but the app does not hold it — unexplained (try Refresh)</li>"
                    )
                for path in result.stale_in_app:
                    items.append(
                        f"<li><b>{result.repo_name}</b>: the app shows <code>{path}</code> as "
                        "changed but git no longer reports it — stale (try Refresh)</li>"
                    )
            items.extend(f"<li>{line}</li>" for line in render_loss_lines)
            html = (
                f"<p>{total_repos} repos, {total_files} files — "
                f"{len(items)} discrepancy(ies) found:</p><ul>{''.join(items)}</ul>"
            )

        HelpDialog("Verify Changes Against Git", html, self).exec()

    def _explain_missing_from_tree(self, repo: Repository, change: FileChange) -> str | None:
        """Best-effort explanation for why a change that should have survived
        the active filters (per filter_workspace) isn't in the rendered tree
        — mirrors the individual filter checks in workspace_filter.py plus
        the tree model's own nested-repo suppression. Returns None ("unexplained")
        when no known mechanism accounts for the gap — that's the signal that
        actually matters, since it means something is genuinely inconsistent."""
        if self._ignore_md_action.isChecked() and change.path.suffix.lower() == ".md":
            return "hidden by the 'Ignore MD files' setting"

        for rule in self._folder_filter_rules:
            if not change.is_directory and rule.matches_path(change.path.as_posix()):
                return f"hidden by folder filter {rule.mode.value}:{rule.text!r}"
            parts = change.path.parts if change.is_directory else change.path.parts[:-1]
            if any(rule.matches(folder_name) for folder_name in parts):
                return f"hidden by folder filter {rule.mode.value}:{rule.text!r}"

        if self._time_filter_minutes > 0:
            try:
                mtime = (repo.path / change.path).stat().st_mtime
                age_minutes = (time.time() - mtime) / 60
                if age_minutes > self._time_filter_minutes:
                    return (
                        "older than the last-commit-time filter "
                        f"({self._time_filter_minutes} min)"
                    )
            except OSError:
                pass

        if change.is_directory and self._workspace is not None:
            nested_path = repo.path / change.path
            if any(r.path == nested_path for r in self._workspace.repositories):
                return "shown as its own nested repository row instead"

        return None

    def _on_file_selected(self, repo_path: Path, change: FileChange) -> None:
        if self._restoring_selection:
            # We're here because _restore_previous_selection() below is
            # putting the highlight back on the file the user is staying on
            # -- without this early return, that programmatic selection
            # change would re-fire this very handler and re-ask the same
            # "Discard edits?" question for the file we're restoring.
            return
        if self._diff_view.has_unsaved_edits():
            reply = QMessageBox.question(
                self,
                "Discard edits?",
                "You have unsaved edits to this file. Discard them and switch files?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                # Qt has already moved the tree/list's own selection
                # highlight onto `change` by the time this signal reaches
                # us (it fires from currentChanged, after the current index
                # has changed) -- declining here must not leave that row
                # highlighted while the diff view still shows the file the
                # user chose to keep editing.
                self._restore_previous_selection()
                return
            self._diff_view.discard_edits_if_any()
            self.statusBar().showMessage("Discarded unsaved edits", 5000)
        self._selected_change = change
        self._selected_repo_path = repo_path
        self._update_file_info_label(repo_path, change)
        if change.diff is not None and not self._always_reload_diff_action.isChecked():
            abs_path, not_editable_reason = self._edit_target(repo_path, change)
            self._diff_view.set_diff(change.diff, str(change.path), abs_path, not_editable_reason)
            return
        self._load_diff(repo_path, change)

    def _restore_previous_selection(self) -> None:
        """Bug 4: puts the tree/list highlight back on the file still open
        for editing after the user declines to discard its edits. Whichever
        widget emitted the file_selected we're currently handling (found via
        sender(), since both _tree_view and _aggregate_list route through
        the same _on_file_selected) is the one whose highlight needs
        correcting -- the other one never moved.
        """
        if self._selected_repo_path is None or self._selected_change is None:
            return
        source = self.sender()
        self._restoring_selection = True
        try:
            if source is self._tree_view:
                index = self._find_tree_index(
                    self._tree_view.model(), self._selected_repo_path, self._selected_change
                )
                if index.isValid():
                    self._tree_view.setCurrentIndex(index)
            elif source is self._aggregate_list:
                for row in range(self._aggregate_list.count()):
                    item = self._aggregate_list.item(row)
                    # Matches aggregate_list.py's own _ITEM_DATA_ROLE
                    # (Qt.ItemDataRole.UserRole + 1), which stores each
                    # row's (repo_path, change) tuple -- not re-exported
                    # from that module, so the literal role value is
                    # duplicated here rather than importing a private name.
                    data = item.data(Qt.ItemDataRole.UserRole + 1)
                    if (
                        data is not None
                        and data[0] == self._selected_repo_path
                        and data[1] is self._selected_change
                    ):
                        self._aggregate_list.setCurrentItem(item)
                        break
        finally:
            self._restoring_selection = False

    @staticmethod
    def _find_tree_index(
        model, repo_path: Path, change: FileChange, parent: QModelIndex = QModelIndex()
    ) -> QModelIndex:
        """Recursively searches the folder tree's (proxied) model for the
        row carrying `change`, using the same FILE_CHANGE_ROLE/REPO_PATH_ROLE
        the tree model already exposes -- plain public QAbstractItemModel
        API (rowCount/index/data), not a reach into tree_view.py's private
        attributes.
        """
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            if index.data(FILE_CHANGE_ROLE) is change and index.data(REPO_PATH_ROLE) == str(
                repo_path
            ):
                return index
            found = MainWindow._find_tree_index(model, repo_path, change, index)
            if found.isValid():
                return found
        return QModelIndex()

    def _edit_target(self, repo_path: Path, change: FileChange) -> tuple[Path | None, str | None]:
        """Whether `change` can be edited in place, and if not, why -- as a
        single (path, reason) pair so the two can never drift apart.
        Exactly one of the two is None.

        `is_unpushed_commit` is checked first: that diff is old_ref=<upstream>,
        new_ref=HEAD (see git_repo_adapter.compute_diff), so the file on disk
        is the CURRENT working tree, not either side of the displayed diff --
        loading it into the edit pane would show content that matches
        neither ref, and Save would silently overwrite it with something
        unrelated to what the diff shown. This must be checked before
        DELETED/directory, but the current change types can't overlap
        (only a real file's own commits carry is_unpushed_commit).
        """
        if change.is_unpushed_commit:
            return None, (
                "This is an already-committed (not yet pushed) change, so the file "
                "on disk no longer matches this diff."
            )
        if change.change_type == ChangeType.DELETED:
            return None, "The file no longer exists."
        if change.is_directory:
            return None, "This is a folder, not a file."
        return repo_path / change.path, None

    def _update_file_info_label(self, repo_path: Path, change: FileChange) -> None:
        if change.change_type == ChangeType.DELETED:
            self._file_info_label.setText("Deleted")
            return
        try:
            with (repo_path / change.path).open("rb") as file:
                content = file.read(_FILE_INFO_SNIFF_BYTES)
        except OSError:
            self._file_info_label.setText("")
            return
        encoding = detect_encoding(content)
        line_ending = detect_line_ending(content)
        self._file_info_label.setText(f"{encoding} · {line_ending}")

    def _load_diff(self, repo_path: Path, change: FileChange) -> None:
        # confirm_and_clear_diff() (not the unconditional clear_diff())
        # because this is the single choke point both "Refresh Diff" and
        # "Ignore Whitespace" funnel through (_on_refresh_diff,
        # _on_ignore_whitespace_toggled) -- neither used to consult unsaved
        # edits at all before this reload wiped them. By the time
        # _on_file_selected's own call here runs, any unsaved edits were
        # already confirmed/discarded there, so has_unsaved_edits() reads
        # False and this is a silent no-op guard for that path -- no
        # double prompt.
        if not self._diff_view.confirm_and_clear_diff():
            return
        worker = DiffWorker(
            repo_path, change, ignore_whitespace=self._ignore_whitespace_action.isChecked()
        )
        worker.signals.diff_ready.connect(self._guard_worker_result(self._on_diff_ready))
        worker.signals.error.connect(
            self._guard_worker_result(lambda message: self._on_diff_error(message, change.path))
        )
        start_worker(self._thread_pool, worker)

    def _on_diff_ready(self, change: FileChange, diff) -> None:
        change.diff = diff
        if change is self._selected_change and self._selected_repo_path is not None:
            abs_path, not_editable_reason = self._edit_target(self._selected_repo_path, change)
            self._diff_view.set_diff(diff, str(change.path), abs_path, not_editable_reason)

    def _on_diff_error(self, message: str, file_path: Path) -> None:
        self._report_error(f"Diff failed for {file_path}: {message}")

    def _on_file_saved(self, file_path: str) -> None:
        applog.log(f"Saved edits to {file_path}", level=applog.LogLevel.INFO)
        self.statusBar().showMessage(f"Saved {file_path}", 5000)
        self._on_refresh()

    def _on_ignore_whitespace_toggled(self, checked: bool) -> None:
        applog.log(f"Ignore whitespace: {checked}", level=applog.LogLevel.INFO)
        if self._workspace is not None:
            for repo in self._workspace.repositories:
                for change in repo.changes:
                    change.diff = None
        if self._selected_change is not None and self._selected_repo_path is not None:
            self._load_diff(self._selected_repo_path, self._selected_change)

    def _on_display_filter_toggled(self, checked: bool) -> None:
        action = self.sender()
        name = action.text() if action is not None else "display filter"
        applog.log(f"{name}: {checked}", level=applog.LogLevel.INFO)
        if self._restoring_settings:
            return
        self._refresh_display()

    def _on_toggle_time_filter(self) -> None:
        self._diff_view.set_time_filter_minutes(0 if self._time_filter_minutes else 30)

    def _on_time_filter_changed(self, minutes: int) -> None:
        applog.log(f"Time filter changed: {minutes} minute(s)", level=applog.LogLevel.INFO)
        self._time_filter_minutes = minutes
        self._update_status_extra_label()
        self._refresh_display()

    def _on_sync_scroll_toggled(self, checked: bool) -> None:
        applog.log(f"Sync side-by-side scroll: {checked}", level=applog.LogLevel.INFO)
        self._diff_view.set_sync_scroll(checked)

    def _on_always_reload_diff_toggled(self, checked: bool) -> None:
        applog.log(f"Always reload fresh diff: {checked}", level=applog.LogLevel.INFO)

    def _on_configure_auto_refresh(self) -> None:
        current = self._settings.auto_refresh_minutes()
        minutes, ok = QInputDialog.getInt(
            self,
            "Auto Refresh",
            "Refresh interval in minutes (0 = disabled):",
            current,
            0,
            1440,
        )
        if not ok:
            return
        self._set_auto_refresh_minutes(minutes)

    def _set_auto_refresh_minutes(self, minutes: int) -> None:
        """Persists and applies the auto-refresh interval. Shared by the
        Auto Refresh… dialog and SettingsDialog's spinbox so both surfaces
        go through the same persist+apply path (D1)."""
        applog.log(f"Set auto refresh interval: {minutes} minute(s)", level=applog.LogLevel.INFO)
        self._settings.set_auto_refresh_minutes(minutes)
        self._apply_auto_refresh_interval(minutes)

    def _apply_auto_refresh_interval(self, minutes: int) -> None:
        self._auto_refresh_minutes = minutes
        self._update_status_extra_label()
        self._auto_refresh_timer.stop()
        if minutes > 0:
            self._auto_refresh_timer.start(minutes * 60 * 1000)

    def _update_status_extra_label(self) -> None:
        parts = []
        if self._showing_stale_cache:
            parts.append("Showing cached results — rescanning…")
        if self._active_profile_name:
            parts.append(f"Profile: {self._active_profile_name}")
        if self._auto_refresh_minutes:
            parts.append(f"Auto refresh: {self._auto_refresh_minutes} min")
        if self._time_filter_minutes:
            parts.append(f"Last commit: {self._time_filter_minutes} min")
        self._status_extra_label.setText("  |  ".join(parts))

    def _on_auto_refresh_timeout(self) -> None:
        if self._root_folder and not self._scan_in_progress:
            dirty_paths = self._collect_dirty_paths()
            self._start_scan(self._root_folder, auto_refresh=True, dirty_paths=dirty_paths)

    def _on_use_file_watcher_toggled(self, checked: bool) -> None:
        applog.log(f"Watch for file changes: {checked}", level=applog.LogLevel.INFO)
        self._settings.set_use_file_watcher(checked)
        if checked:
            if self._workspace is not None:
                self._refresh_watch_paths(self._workspace.repositories)
        else:
            self._file_watcher.stop()

    def _refresh_watch_paths(self, repositories: list[Repository]) -> None:
        repo_paths = [r.path for r in repositories]
        worker = WatchPathsWorker(repo_paths)
        worker.signals.succeeded.connect(
            self._guard_worker_result(self._file_watcher.set_watch_paths)
        )
        start_worker(self._thread_pool, worker)
        # Directory watching alone (above) can't see an in-place edit to a
        # file that's already tracked as changed — only a create/delete/
        # rename in the directory fires directoryChanged. Watching each
        # already-changed file individually closes that gap via fileChanged
        # (Part 3 of the stale-cache fix). Cheap to compute directly here:
        # no filesystem walk needed, just the paths already on `repositories`.
        changed_file_paths = [
            r.path / change.path
            for r in repositories
            for change in r.changes
            if not change.is_directory
        ]
        self._file_watcher.set_watched_files(changed_file_paths)

    def _on_file_watcher_changed(self) -> None:
        if self._root_folder and not self._scan_in_progress:
            applog.log("File change detected, refreshing", level=applog.LogLevel.DEBUG)
            dirty_paths = self._collect_dirty_paths()
            self._start_scan(self._root_folder, auto_refresh=True, dirty_paths=dirty_paths)

    def _collect_dirty_paths(self) -> set[Path] | None:
        if self._workspace is None:
            return None
        return self._file_watcher.dirty_repo_roots(
            repo_paths=[r.path for r in self._workspace.repositories]
        )

    def _on_configure_log_level(self) -> None:
        levels = applog.LOG_LEVEL_NAMES
        current = self._settings.log_level()
        current_index = levels.index(current) if current in levels else levels.index("INFO")
        level_name, ok = QInputDialog.getItem(
            self,
            "Log Level",
            "Log level:",
            levels,
            current_index,
            editable=False,
        )
        if not ok:
            return
        self._set_log_level(level_name)

    def _set_log_level(self, level_name: str) -> None:
        """Persists and applies the log level. Shared by the Log Level…
        dialog and SettingsDialog's combo box (D1)."""
        applog.log(f"Set log level: {level_name}", level=applog.LogLevel.INFO)
        self._settings.set_log_level(level_name)
        applog.set_level(applog.level_from_name(level_name))

    def _on_configure_tooltip_font_size(self) -> None:
        current = self._settings.tooltip_font_size() or 9
        size, ok = QInputDialog.getInt(
            self,
            "Tooltip Font Size",
            "Tooltip font size in points (0 = system default):",
            current,
            0,
            36,
        )
        if not ok:
            return
        self._set_tooltip_font_size(size)

    def _set_tooltip_font_size(self, size: int) -> None:
        """Persists and applies the tooltip font size. Shared by the
        Tooltip Font Size… dialog and SettingsDialog's spinbox (D1)."""
        applog.log(f"Set tooltip font size: {size}", level=applog.LogLevel.INFO)
        self._settings.set_tooltip_font_size(size)
        self._apply_tooltip_font_size(size)

    def _apply_tooltip_font_size(self, size: int) -> None:
        app = QApplication.instance()
        if app is None:
            return
        app.setStyleSheet(f"QToolTip {{ font-size: {size}pt; }}" if size > 0 else "")

    def _github_log(self, message: str) -> None:
        applog.log(f"GitHub: {message}", level=applog.LogLevel.INFO)

    def _auto_connect_github(self) -> None:
        username = self._settings.github_username()
        if username is None:
            applog.log("GitHub auto-connect skipped: no stored account", level=applog.LogLevel.DEBUG)
            return
        token = github_auth.get_token(username)
        if token is None:
            applog.log(
                f"GitHub auto-connect skipped: no stored token for {username}",
                level=applog.LogLevel.WARNING,
            )
            return
        applog.log(f"Connected to GitHub as {username}", level=applog.LogLevel.INFO)
        self.statusBar().showMessage(f"Connected to GitHub as {username}", 5000)

    def _on_connect_github(self) -> None:
        dialog = GitHubConnectDialog(self._settings.github_username() or "", self)
        if dialog.exec() != GitHubConnectDialog.DialogCode.Accepted:
            return
        username = dialog.username()
        token = dialog.token()
        if not username or not token:
            QMessageBox.warning(self, "Connect to GitHub", "Username and token are required.")
            return

        try:
            authenticated_login = GitHubClient(token, on_log=self._github_log).get_authenticated_login()
        except GitHubError as exc:
            applog.log(f"GitHub connection failed: {exc}", level=applog.LogLevel.ERROR)
            QMessageBox.warning(self, "Connect to GitHub", f"Could not authenticate: {exc}")
            return

        if authenticated_login.lower() != username.lower():
            QMessageBox.warning(
                self,
                "Connect to GitHub",
                f"This token belongs to '{authenticated_login}', not '{username}'.",
            )
            return

        self._settings.set_github_username(authenticated_login)
        github_auth.set_token(authenticated_login, token)
        self._disconnect_github_action.setEnabled(True)
        applog.log(f"Connected to GitHub as {authenticated_login}", level=applog.LogLevel.INFO)
        self.statusBar().showMessage(f"Connected to GitHub as {authenticated_login}", 5000)

    def _on_disconnect_github(self) -> None:
        username = self._settings.github_username()
        if username is None:
            return
        github_auth.delete_token(username)
        self._settings.clear_github_username()
        self._disconnect_github_action.setEnabled(False)
        applog.log(f"Disconnected from GitHub ({username})", level=applog.LogLevel.INFO)
        self.statusBar().showMessage("Disconnected from GitHub", 5000)

    def _github_client(self) -> GitHubClient | None:
        username = self._settings.github_username()
        if username is None:
            return None
        token = github_auth.get_token(username)
        if token is None:
            return None
        return GitHubClient(token, on_log=self._github_log)

    def _on_show_my_pull_requests(self) -> None:
        self._diff_view.set_pull_requests_button_enabled(False)
        if not self._fetch_my_pull_requests():
            self._diff_view.set_pull_requests_button_enabled(True)

    def _on_open_pull_requests_panel(self) -> None:
        self._pr_panel.show()
        self._fetch_my_pull_requests()

    def _on_my_pull_requests_refresh_requested(self) -> None:
        if self._my_pull_requests_dialog is not None:
            self._my_pull_requests_dialog.set_refreshing(True)
        if self._pr_panel.isVisible():
            self._pr_panel.set_refreshing(True)
        self._fetch_my_pull_requests()

    def _fetch_my_pull_requests(self) -> bool:
        username = self._settings.github_username()
        github_client = self._github_client()
        if username is None or github_client is None:
            QMessageBox.information(
                self, "My Open Pull Requests", "Connect to GitHub first (Settings menu)."
            )
            return False
        if self._workspace is None or not self._workspace.repositories:
            QMessageBox.information(self, "My Open Pull Requests", "No repositories loaded.")
            return False

        owner_repo_pairs = []
        seen_owner_repo = set()
        for repo in self._workspace.repositories:
            remote_url = GitRepoAdapter(repo.path).get_remote_url("origin")
            if remote_url is None:
                applog.log(f"GitHub: {repo.name} has no 'origin' remote, skipping", level=applog.LogLevel.DEBUG)
                continue
            owner_repo = parse_github_owner_repo(remote_url)
            if owner_repo is None:
                applog.log(
                    f"GitHub: {repo.name} remote '{remote_url}' is not a recognized GitHub URL, skipping",
                    level=applog.LogLevel.DEBUG,
                )
                continue
            if owner_repo in seen_owner_repo:
                continue
            seen_owner_repo.add(owner_repo)
            owner_repo_pairs.append(owner_repo)

        applog.log(
            f"GitHub: resolved {len(owner_repo_pairs)} GitHub repo(s) from tree: {owner_repo_pairs}",
            level=applog.LogLevel.INFO,
        )

        if not owner_repo_pairs:
            QMessageBox.information(
                self, "My Open Pull Requests", "No GitHub repositories found in the tree."
            )
            return False

        applog.log("My Open Pull Requests", level=applog.LogLevel.INFO)
        self.statusBar().showMessage("Fetching your open pull requests…")
        worker = MyPullRequestsWorker(github_client, username, owner_repo_pairs)
        worker.signals.succeeded.connect(
            self._guard_worker_result(self._on_my_pull_requests_ready)
        )
        worker.signals.error.connect(self._guard_worker_result(self._on_my_pull_requests_error))
        worker.signals.progress.connect(self._guard_worker_result(self._on_scan_progress))
        start_worker(self._thread_pool, worker)
        return True

    def _on_my_pull_requests_ready(self, pull_requests: list) -> None:
        self.statusBar().clearMessage()
        if self._pr_panel.isVisible():
            self._pr_panel.set_pull_requests(pull_requests)
            self._pr_panel.set_refreshing(False)

        if self._my_pull_requests_dialog is not None:
            self._my_pull_requests_dialog.set_pull_requests(pull_requests)
            self._my_pull_requests_dialog.set_refreshing(False)
            return

        if self._pr_panel.isVisible():
            self._diff_view.set_pull_requests_button_enabled(True)
            return

        dialog = MyPullRequestsDialog(pull_requests, self)
        dialog.refresh_requested.connect(self._on_my_pull_requests_refresh_requested)
        dialog.pull_request_refresh_requested.connect(self._on_pull_request_refresh_requested)
        dialog.pull_request_info_requested.connect(self._on_pull_request_info_requested)
        dialog.pull_request_issues_requested.connect(self._on_pull_request_issues_requested)
        self._my_pull_requests_dialog = dialog
        dialog.exec()
        self._my_pull_requests_dialog = None
        self._diff_view.set_pull_requests_button_enabled(True)

    def _on_my_pull_requests_error(self, message: str) -> None:
        applog.log(f"Failed to fetch open pull requests: {message}", level=applog.LogLevel.ERROR)
        self.statusBar().clearMessage()
        if self._my_pull_requests_dialog is not None:
            self._my_pull_requests_dialog.set_refreshing(False)
        if self._pr_panel.isVisible():
            self._pr_panel.set_refreshing(False)
        self._diff_view.set_pull_requests_button_enabled(True)
        QMessageBox.warning(self, "My Open Pull Requests", f"Failed to fetch: {message}")

    def _on_pull_request_refresh_requested(self, repository: str, number: int) -> None:
        github_client = self._github_client()
        if github_client is None:
            return
        applog.log(f"Refreshing {repository}#{number}", level=applog.LogLevel.INFO)
        self.statusBar().showMessage(f"Refreshing {repository}#{number}…")
        worker = PullRequestRefreshWorker(github_client, repository, number)
        worker.signals.succeeded.connect(
            self._guard_worker_result(self._on_pull_request_refresh_ready)
        )
        worker.signals.error.connect(self._guard_worker_result(self._on_pull_request_action_error))
        start_worker(self._thread_pool, worker)

    def _on_pull_request_refresh_ready(self, repository: str, number: int, result: tuple) -> None:
        approved, unresolved_count, last_reviewer, last_reviewed_at, changed_files, checks_state = result
        self.statusBar().clearMessage()
        if self._my_pull_requests_dialog is not None:
            self._my_pull_requests_dialog.update_pull_request_fields(
                repository,
                number,
                approved=approved,
                unresolved_review_thread_count=unresolved_count,
                last_reviewer=last_reviewer,
                last_reviewed_at=last_reviewed_at,
                changed_files=changed_files,
                checks_state=checks_state,
            )
        if self._pr_panel.isVisible():
            self._pr_panel.update_pull_request_fields(
                repository,
                number,
                approved=approved,
                unresolved_review_thread_count=unresolved_count,
                last_reviewer=last_reviewer,
                last_reviewed_at=last_reviewed_at,
                changed_files=changed_files,
                checks_state=checks_state,
            )

    def _on_pull_request_info_requested(self, repository: str, number: int) -> None:
        github_client = self._github_client()
        if github_client is None:
            return
        applog.log(f"Fetching info for {repository}#{number}", level=applog.LogLevel.INFO)
        self.statusBar().showMessage(f"Fetching info for {repository}#{number}…")
        worker = PullRequestDetailsWorker(github_client, repository, number)
        worker.signals.succeeded.connect(
            self._guard_worker_result(self._on_pull_request_details_ready)
        )
        worker.signals.error.connect(self._guard_worker_result(self._on_pull_request_action_error))
        start_worker(self._thread_pool, worker)

    def _on_pull_request_details_ready(self, details) -> None:
        self.statusBar().clearMessage()
        PullRequestInfoDialog(details, self).exec()

    def _on_pull_request_issues_requested(self, repository: str, number: int) -> None:
        github_client = self._github_client()
        if github_client is None:
            return
        applog.log(f"Fetching open issues for {repository}#{number}", level=applog.LogLevel.INFO)
        self.statusBar().showMessage(f"Fetching open issues for {repository}#{number}…")
        worker = PullRequestThreadsWorker(github_client, repository, number)
        worker.signals.succeeded.connect(
            self._guard_worker_result(self._on_pull_request_threads_ready)
        )
        worker.signals.error.connect(self._guard_worker_result(self._on_pull_request_action_error))
        start_worker(self._thread_pool, worker)

    def _on_pull_request_threads_ready(self, number: int, threads: list) -> None:
        self.statusBar().clearMessage()
        PullRequestIssuesDialog(threads, number, self).exec()

    def _on_pull_request_action_error(self, message: str) -> None:
        applog.log(f"Pull request action failed: {message}", level=applog.LogLevel.ERROR)
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "Pull Request", f"Action failed: {message}")

    def _on_open_settings_dialog(self) -> None:
        dialog = SettingsDialog(self, self)
        dialog.exec()

    def _on_manage_folder_filters(self) -> None:
        dialog = FolderFilterDialog(self._folder_filter_rules, self)
        dialog.rules_changed.connect(self._on_folder_filter_rules_changed)
        dialog.exec()

    def _on_folder_filter_rules_changed(self, rules: list[FolderFilterRule]) -> None:
        rules_desc = ", ".join(f"{r.mode.value}:{r.text!r}" for r in rules)
        applog.log(
            f"Folder filter rules changed ({len(rules)}): [{rules_desc}]",
            level=applog.LogLevel.INFO,
        )
        self._folder_filter_rules = rules
        self._settings.set_folder_filter_rules(rules)
        self._refresh_display()

    def _on_manage_profiles(self) -> None:
        available_repo_names = (
            sorted(
                {
                    repo.name
                    for repo in self._workspace.repositories
                    if repo.logical_parent_path is None
                }
            )
            if self._workspace is not None
            else []
        )
        dialog = ProfileDialog(self._profiles, available_repo_names, self)
        dialog.profiles_changed.connect(self._on_profiles_changed)
        dialog.exec()

    def _on_profiles_changed(self, profiles: list[Profile]) -> None:
        applog.log(f"Profiles changed ({len(profiles)})", level=applog.LogLevel.INFO)
        self._profiles = profiles
        self._settings.set_profiles(profiles)
        if self._active_profile_name is not None and not any(
            p.name == self._active_profile_name for p in profiles
        ):
            self._active_profile_name = None
            self._settings.set_active_profile_name(None)
            self._update_status_extra_label()
        self._rebuild_profile_menu()
        self._refresh_display()

    def _rebuild_profile_menu(self) -> None:
        self._profile_menu.clear()
        for action in self._profile_action_group.actions():
            self._profile_action_group.removeAction(action)

        no_profile_action = QAction("No Profile", self, checkable=True)
        no_profile_action.setChecked(self._active_profile_name is None)
        no_profile_action.triggered.connect(lambda: self._on_profile_selected(None))
        self._profile_action_group.addAction(no_profile_action)
        self._profile_menu.addAction(no_profile_action)

        if self._profiles:
            self._profile_menu.addSeparator()
        for profile in self._profiles:
            action = QAction(profile.name, self, checkable=True)
            action.setChecked(profile.name == self._active_profile_name)
            action.triggered.connect(lambda _checked, name=profile.name: self._on_profile_selected(name))
            self._profile_action_group.addAction(action)
            self._profile_menu.addAction(action)

    def _on_profile_selected(self, name: str | None) -> None:
        applog.log(f"Active profile changed: {name!r}", level=applog.LogLevel.INFO)
        self._active_profile_name = name
        self._settings.set_active_profile_name(name)
        self._update_status_extra_label()
        self._refresh_display()

    def _active_profile(self) -> Profile | None:
        if self._active_profile_name is None:
            return None
        return next((p for p in self._profiles if p.name == self._active_profile_name), None)

    def _on_add_repo_to_profile(self, repo_name: str, profile_name: str) -> None:
        profile = next((p for p in self._profiles if p.name == profile_name), None)
        if profile is None or repo_name in profile.repo_names:
            return
        profile.repo_names.append(repo_name)
        applog.log(f"Added {repo_name!r} to profile {profile_name!r}", level=applog.LogLevel.INFO)
        self._settings.set_profiles(self._profiles)
        if profile_name == self._active_profile_name:
            self._refresh_display()

    def _on_remove_repo_from_profile(self, repo_name: str, profile_name: str) -> None:
        profile = next((p for p in self._profiles if p.name == profile_name), None)
        if profile is None or repo_name not in profile.repo_names:
            return
        profile.repo_names.remove(repo_name)
        applog.log(f"Removed {repo_name!r} from profile {profile_name!r}", level=applog.LogLevel.INFO)
        self._settings.set_profiles(self._profiles)
        if profile_name == self._active_profile_name:
            self._refresh_display()

    def _on_new_profile_with_repo(self, repo_name: str) -> None:
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        name = name.strip()
        if not ok or not name:
            return
        if any(p.name == name for p in self._profiles):
            QMessageBox.warning(self, "Profiles", f"A profile named {name!r} already exists.")
            return
        self._profiles.append(Profile(name=name, repo_names=[repo_name]))
        self._settings.set_profiles(self._profiles)
        self._rebuild_profile_menu()
        applog.log(f"Created profile {name!r} with repo {repo_name!r}", level=applog.LogLevel.INFO)

    def _on_copy_app_log(self) -> None:
        text = "\n".join(applog.all_entries())
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage("App log copied to clipboard", 3000)

    def _report_error(self, message: str) -> None:
        """Single funnel for every user-facing error: logs it (feeding the
        persistent indicator below via applog's ERROR-only store), shows the
        same transient 5000ms toast callers used to show directly, and
        refreshes the indicator immediately rather than waiting for
        _error_indicator_timer's next tick. Call sites that used to call
        applog.log(..., level=ERROR) and showMessage(...) separately now do
        neither -- doing both here is what prevents the same error being
        logged twice.
        """
        applog.log(message, level=applog.LogLevel.ERROR)
        self.statusBar().showMessage(message, 5000)
        self._refresh_error_indicator()

    def _refresh_error_indicator(self) -> None:
        count = applog.error_count()
        if count == 0:
            self._error_indicator_button.setVisible(False)
            return
        noun = "error" if count == 1 else "errors"
        self._error_indicator_button.setText(f"⚠ {count} {noun}")
        recent = applog.recent_errors()
        if recent:
            self._error_indicator_button.setToolTip(recent[0])
        self._error_indicator_button.setVisible(True)

    def _on_show_error_log(self) -> None:
        dialog = ErrorLogDialog(self, on_cleared=self._refresh_error_indicator)
        dialog.exec()
        # Covers the Close/window-X path too, not just Clear -- harmless
        # no-op refresh when nothing changed.
        self._refresh_error_indicator()

    def _on_copy_diff(self) -> None:
        if self._selected_change is None or self._selected_change.diff is None:
            self.statusBar().showMessage("No diff to copy", 3000)
            return
        text = format_unified_diff(self._selected_change.diff, str(self._selected_change.path))
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage("Diff copied to clipboard", 3000)

    def _on_copy_file_path(self) -> None:
        if self._selected_change is None or self._selected_repo_path is None:
            self.statusBar().showMessage("No file selected", 3000)
            return
        path = self._selected_repo_path / self._selected_change.path
        QGuiApplication.clipboard().setText(str(path))
        self.statusBar().showMessage("File path copied to clipboard", 3000)

    def _on_copy_file_name(self) -> None:
        if self._selected_change is None:
            self.statusBar().showMessage("No file selected", 3000)
            return
        QGuiApplication.clipboard().setText(self._selected_change.path.name)
        self.statusBar().showMessage("File name copied to clipboard", 3000)

    def _on_tree_context_menu(self, pos) -> None:
        index = self._tree_view.indexAt(pos)
        if not index.isValid():
            return

        if index.data(FILE_CHANGE_ROLE) is not None:
            self._tree_view.setCurrentIndex(index)
            menu = QMenu(self._tree_view)
            menu.addAction("Copy Path", self._on_copy_file_path)
            menu.addAction("Copy Name", self._on_copy_file_name)
            menu.addAction("Refresh Diff", self._on_refresh_diff)
            menu.addAction("Create patch", self._on_create_patch_for_file)
            menu.addSeparator()
            menu.addAction("Filter Out This File", self._on_filter_out_file)
            menu.exec(self._tree_view.viewport().mapToGlobal(pos))
            return

        folder_path = index.data(FOLDER_PATH_ROLE)
        if folder_path is not None:
            is_repo_root = index.data(NODE_KEY_ROLE) == folder_path
            menu = QMenu(self._tree_view)
            menu.addAction("Copy Name", lambda: self._on_copy_folder_name(folder_path))
            menu.addAction("Copy Path", lambda: self._on_copy_folder_path(folder_path))
            menu.addAction(
                "Filter Out This Folder", lambda: self._on_filter_out_folder(folder_path)
            )
            menu.addAction(
                "Create patch", lambda: self._on_create_patch_for_folder(folder_path)
            )
            menu.addSeparator()
            menu.addAction(
                "Expand All", lambda: self._tree_view.expand_index_recursive(index)
            )
            menu.addAction(
                "Collapse All", lambda: self._tree_view.collapse_index_recursive(index)
            )
            if is_repo_root:
                menu.addSeparator()
                menu.addAction(
                    "Refresh Repo", lambda: self._on_refresh_repo(Path(folder_path))
                )
                menu.addAction(
                    "Show Log", lambda: self._on_show_log(Path(folder_path))
                )
                menu.addAction(
                    "Show stashes...", lambda: self._on_show_stashes_for_repo(folder_path)
                )
                menu.addAction(
                    "List Worktrees", lambda: self._on_list_worktrees(Path(folder_path))
                )
                menu.addAction(
                    "Apply patch...", lambda: self._on_apply_patch_for_repo(folder_path)
                )
                repo = self._find_repository(Path(folder_path))
                if repo is not None and repo.logical_parent_path is not None:
                    menu.addSeparator()
                    running = Path(folder_path) in self._worktree_terminal_windows
                    menu.addAction(
                        "Start", lambda: self._on_start_worktree(Path(folder_path))
                    ).setEnabled(not running)
                    menu.addAction(
                        "Stop", lambda: self._on_stop_worktree(Path(folder_path))
                    ).setEnabled(running)
            if not index.parent().isValid():
                repo_name = Path(folder_path).name
                menu.addSeparator()
                self._add_profile_submenu(menu, repo_name)
            menu.exec(self._tree_view.viewport().mapToGlobal(pos))

    def _add_profile_submenu(self, menu: QMenu, repo_name: str) -> None:
        submenu = menu.addMenu("Add to Profile")
        for profile in self._profiles:
            action = submenu.addAction(profile.name)
            action.setCheckable(True)
            action.setChecked(repo_name in profile.repo_names)
            action.toggled.connect(
                lambda checked, name=profile.name: (
                    self._on_add_repo_to_profile(repo_name, name)
                    if checked
                    else self._on_remove_repo_from_profile(repo_name, name)
                )
            )
        if self._profiles:
            submenu.addSeparator()
        submenu.addAction("New Profile…", lambda: self._on_new_profile_with_repo(repo_name))

    def _on_copy_folder_name(self, folder_path: str) -> None:
        QGuiApplication.clipboard().setText(Path(folder_path).name)
        self.statusBar().showMessage("Folder name copied to clipboard", 3000)

    def _on_copy_folder_path(self, folder_path: str) -> None:
        QGuiApplication.clipboard().setText(folder_path)
        self.statusBar().showMessage("Folder path copied to clipboard", 3000)

    def _on_filter_out_file(self) -> None:
        if self._selected_change is None:
            self.statusBar().showMessage("No file selected", 3000)
            return
        relative_path = self._selected_change.path.as_posix()
        self._add_folder_filter_rule(
            FolderFilterRule(text=relative_path, mode=FolderFilterMode.FILE_PATH),
            f"Filtered out {relative_path}",
        )

    def _on_filter_out_folder(self, folder_path: str) -> None:
        folder_name = Path(folder_path).name
        self._add_folder_filter_rule(
            FolderFilterRule(text=folder_name, mode=FolderFilterMode.EQUALS),
            f"Filtered out folder '{folder_name}'",
        )

    def _add_folder_filter_rule(self, rule: FolderFilterRule, status_message: str) -> None:
        if rule in self._folder_filter_rules:
            self.statusBar().showMessage("Filter rule already exists", 3000)
            return
        self._on_folder_filter_rules_changed([*self._folder_filter_rules, rule])
        self.statusBar().showMessage(status_message, 3000)

    def _on_show_log(self, repo_path: Path) -> None:
        applog.log(f"Show Log: {repo_path}", level=applog.LogLevel.INFO)
        dialog = CommitLogDialog(repo_path, parent=self)
        dialog.exec()

    def _on_list_worktrees(self, repo_path: Path) -> None:
        applog.log(f"List Worktrees: {repo_path}", level=applog.LogLevel.INFO)
        dialog = WorktreesDialog(repo_path, parent=self)
        dialog.exec()
        if dialog.deleted_any:
            self._on_refresh()

    def _on_show_stashes_for_repo(self, folder_path: str) -> None:
        repo_path = Path(folder_path)
        applog.log(f"Show Stashes: {repo_path}", level=applog.LogLevel.INFO)
        dialog = StashesDialog(repo_path, parent=self)
        dialog.exec()
        if dialog.restored:
            self._on_refresh_repo(repo_path)

    def _find_repository(self, repo_path: Path) -> Repository | None:
        if self._workspace is None:
            return None
        return next(
            (r for r in self._workspace.repositories if r.path == repo_path), None
        )

    def _find_owning_repository(self, folder_path: Path) -> Repository | None:
        # A folder can sit inside more than one repo's path when a nested repo
        # (e.g. a worktree) lives under its parent -- the deepest (longest
        # path) match is the one that actually owns the folder's files, so a
        # nested repo's own changes never get attributed to its parent's patch.
        if self._workspace is None:
            return None
        candidates = [
            r for r in self._workspace.repositories if folder_path.is_relative_to(r.path)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda r: len(r.path.parts))

    def _on_create_patch_for_file(self) -> None:
        if self._selected_change is None or self._selected_repo_path is None:
            self.statusBar().showMessage("No file selected", 3000)
            return
        # RepoTreeView._on_current_changed emits file_selected's repo_path
        # straight from REPO_PATH_ROLE (a str), not wrapped in Path -- so
        # _selected_repo_path is a str at runtime despite its `Path | None`
        # annotation. `str / Path` happens to work (PurePath.__rtruediv__),
        # which is why _on_copy_file_path never noticed, but Path.__eq__
        # against a str is unconditionally False, so _find_repository would
        # never match here without normalizing first.
        repo_path = Path(self._selected_repo_path)
        repo = self._find_repository(repo_path)
        if repo is None:
            self.statusBar().showMessage("Could not find repository for patch", 3000)
            return
        applog.log(
            f"Create Patch: {repo_path / self._selected_change.path}",
            level=applog.LogLevel.INFO,
        )
        self._create_patch_with_selection(
            repo, self._selected_change.path, f"{self._selected_change.path.name}.patch"
        )

    def _on_create_patch_for_folder(self, folder_path: str) -> None:
        repo = self._find_owning_repository(Path(folder_path))
        if repo is None:
            self.statusBar().showMessage("Could not find repository for patch", 3000)
            return
        applog.log(f"Create Patch: {folder_path}", level=applog.LogLevel.INFO)
        relpath = Path(folder_path).relative_to(repo.path)
        self._create_patch_with_selection(repo, relpath, f"{Path(folder_path).name}.patch")

    def _create_patch_with_selection(
        self, repo: Repository, target_relpath: Path, suggested_file_name: str
    ) -> None:
        """Shared by both "Create patch" menu entries (file row and folder/
        repo-root row): resolve what's in scope, let the user de-select what
        they don't want, then build the patch for only what's left checked.

        A single-file target still goes through the dialog (it lists one
        checked row) rather than skipping straight to the old immediate-build
        behavior -- taking the user's own words literally beats a "helpful"
        special case, and it means there's exactly one path to test instead
        of two that can silently drift apart.
        """
        changes = self._patch_service.files_in_scope(repo, target_relpath)
        if not changes:
            # Same "nothing to patch" info message _present_patch already
            # shows for an empty patch string -- reused rather than
            # duplicated, since an empty scope and an empty patch are the
            # same user-facing outcome.
            self._present_patch("", suggested_file_name)
            return

        dialog = PatchFileSelectionDialog(changes, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        patch = self._patch_service.build_patch(repo, dialog.selected_paths())
        self._present_patch(patch, suggested_file_name)

    def _present_patch(self, patch: str, suggested_file_name: str) -> None:
        if not patch:
            QMessageBox.information(self, "Create Patch", "No changes to patch.")
            return

        chooser = QMessageBox(self)
        chooser.setWindowTitle("Create Patch")
        chooser.setText("Patch generated. Where would you like to send it?")
        copy_button = chooser.addButton(
            "Copy to Clipboard", QMessageBox.ButtonRole.AcceptRole
        )
        save_button = chooser.addButton("Save to Disk…", QMessageBox.ButtonRole.ActionRole)
        cancel_button = chooser.addButton(QMessageBox.StandardButton.Cancel)
        chooser.setDefaultButton(cancel_button)
        chooser.setEscapeButton(cancel_button)
        chooser.exec()
        clicked = chooser.clickedButton()

        if clicked is copy_button:
            QGuiApplication.clipboard().setText(patch)
            self.statusBar().showMessage("Patch copied to clipboard", 3000)
            return

        if clicked is save_button:
            file_path, _ = QFileDialog.getSaveFileName(
                self, "Save Patch", suggested_file_name, "Patch Files (*.patch)"
            )
            if not file_path:
                return
            try:
                Path(file_path).write_text(patch, encoding="utf-8")
            except OSError as error:
                QMessageBox.warning(self, "Create Patch", f"Failed to save patch: {error}")
                return
            self.statusBar().showMessage(f"Patch saved to {file_path}", 3000)

    def _on_apply_patch_for_repo(self, folder_path: str) -> None:
        """Repo-root-only inverse of "Create patch": ask where the patch
        text comes from, parse it, let the user narrow it down to a subset
        of the files it touches (reusing PatchFileSelectionDialog exactly as
        "Create patch" does), then apply just that subset.
        """
        repo = self._find_owning_repository(Path(folder_path))
        if repo is None:
            self.statusBar().showMessage("Could not find repository to apply patch to", 3000)
            return

        chooser = QMessageBox(self)
        chooser.setWindowTitle("Apply Patch")
        chooser.setText("Where is the patch coming from?")
        file_button = chooser.addButton("From File…", QMessageBox.ButtonRole.AcceptRole)
        clipboard_button = chooser.addButton(
            "From Clipboard", QMessageBox.ButtonRole.ActionRole
        )
        cancel_button = chooser.addButton(QMessageBox.StandardButton.Cancel)
        chooser.setDefaultButton(cancel_button)
        chooser.setEscapeButton(cancel_button)
        chooser.exec()
        clicked = chooser.clickedButton()

        if clicked is file_button:
            patch_text = self._read_patch_from_file()
        elif clicked is clipboard_button:
            patch_text = self._read_patch_from_clipboard()
        else:
            return

        if patch_text is None:
            return

        applog.log(f"Apply Patch: {folder_path}", level=applog.LogLevel.INFO)
        self._apply_patch_with_selection(repo, patch_text, Path(folder_path))

    def _read_patch_from_file(self) -> str | None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Apply Patch", "", "Patch Files (*.patch *.diff)"
        )
        if not file_path:
            return None
        try:
            return Path(file_path).read_text()
        except (OSError, UnicodeDecodeError) as error:
            QMessageBox.warning(self, "Apply Patch", f"Failed to read patch file: {error}")
            return None

    def _read_patch_from_clipboard(self) -> str | None:
        dialog = PatchTextInputDialog(QGuiApplication.clipboard().text(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.patch_text()

    def _apply_patch_with_selection(
        self, repo: Repository, patch_text: str, folder_path: Path
    ) -> None:
        changes = self._patch_service.parse_patch(patch_text)
        if not changes:
            QMessageBox.information(self, "Apply Patch", "No files found in patch.")
            return

        dialog = PatchFileSelectionDialog(changes, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected_paths = dialog.selected_paths()
        try:
            self._patch_service.apply_patch(repo, patch_text, selected_paths)
        except git.GitCommandError as error:
            QMessageBox.critical(self, "Apply Patch", f"Failed to apply patch: {error}")
            return

        QMessageBox.information(
            self, "Apply Patch", f"Applied patch to {len(selected_paths)} file(s)."
        )
        self._on_refresh_repo(folder_path)

    def _on_start_worktree(self, repo_path: Path) -> None:
        applog.log(f"Start Worktree: {repo_path}", level=applog.LogLevel.INFO)
        try:
            window_id = start_worktree_process(repo_path)
        except WorktreeTerminalError as error:
            self._report_error(f"Failed to start {repo_path.name}: {error}")
            return
        self._worktree_terminal_windows[repo_path] = window_id
        self.statusBar().showMessage(f"Started {repo_path.name}", 3000)

    def _on_stop_worktree(self, repo_path: Path) -> None:
        window_id = self._worktree_terminal_windows.pop(repo_path, None)
        if window_id is None:
            return
        applog.log(f"Stop Worktree: {repo_path}", level=applog.LogLevel.INFO)
        stop_worktree_process(window_id)
        self.statusBar().showMessage(f"Stopped {repo_path.name}", 3000)

    def _on_refresh_repo(self, repo_path: Path) -> None:
        if repo_path in self._refreshing_repo_paths:
            # Already refreshing this repo (e.g. a double-click on the row's
            # refresh button, or the button plus the context-menu action) --
            # RepoRefreshWorker itself has no reentrancy guard, so avoid
            # starting a second concurrent scan of the same repo.
            return
        self._refreshing_repo_paths.add(repo_path)
        existing_repo = next(
            (r for r in (self._workspace.repositories if self._workspace else []) if r.path == repo_path),
            None,
        )
        previous_pull_request = None
        logical_parent_path = None
        if existing_repo is not None:
            logical_parent_path = existing_repo.logical_parent_path
            if existing_repo.pull_request is not None:
                previous_pull_request = (
                    existing_repo.pull_request,
                    existing_repo.branch_status.branch_name,
                )
        self.statusBar().showMessage(f"Refreshing {repo_path.name}…")
        worker = RepoRefreshWorker(
            repo_path,
            include_ignored=self._include_ignored_action.isChecked(),
            github_client=self._github_client(),
            previous_pull_request=previous_pull_request,
            logical_parent_path=logical_parent_path,
            include_unpushed_commits=self._include_unpushed_commits_action.isChecked(),
        )
        worker.signals.repo_ready.connect(
            self._guard_worker_result(lambda repo: self._on_repo_refreshed(repo_path, repo))
        )
        worker.signals.error.connect(
            self._guard_worker_result(
                lambda message: self._on_refresh_repo_error(repo_path, message)
            )
        )
        worker.signals.log_message.connect(self._guard_worker_result(self._on_scan_log_message))
        start_worker(self._thread_pool, worker)

    def _on_refresh_repo_error(self, repo_path: Path, message: str) -> None:
        self._refreshing_repo_paths.discard(repo_path)
        self._on_scan_error(message)

    def _on_repo_refreshed(self, repo_path: Path, repo: Repository | None) -> None:
        self._refreshing_repo_paths.discard(repo_path)
        if self._workspace is None:
            return
        if repo is None:
            self._report_error(f"Failed to refresh {repo_path.name}")
            return
        self._workspace.repositories = [
            repo if r.path == repo_path else r for r in self._workspace.repositories
        ]
        self._refresh_display(preserve_tree=True)
        self._tree_view.highlight_repo(repo.path)
        QTimer.singleShot(1500, lambda: self._tree_view.unhighlight_repo(repo.path))
        self.statusBar().showMessage(f"Refreshed {repo_path.name}", 3000)

    def _on_refresh_diff(self) -> None:
        if self._selected_change is None or self._selected_repo_path is None:
            self.statusBar().showMessage("No file selected", 3000)
            return
        self._selected_change.diff = None
        self._load_diff(self._selected_repo_path, self._selected_change)

    def _on_open_in_editor(self) -> None:
        if self._selected_change is None or self._selected_repo_path is None:
            self.statusBar().showMessage("No file selected", 3000)
            return
        path = self._selected_repo_path / self._selected_change.path
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_reveal_in_finder(self) -> None:
        if self._selected_change is None or self._selected_repo_path is None:
            self.statusBar().showMessage("No file selected", 3000)
            return
        path = self._selected_repo_path / self._selected_change.path
        QProcess.startDetached("open", ["-R", str(path)])

    def _set_root_folder(self, folder: str) -> None:
        self._root_folder = folder
        self._settings.set_last_root_folder(folder)
        self._folder_status_label.setText(f"Folder: {folder}")
        self._start_scan(folder)

    def _load_matching_cached_workspace(self, folder: str) -> Workspace | None:
        """Loads the on-disk workspace cache for an instant cold-start paint.

        Only usable for the folder it was captured for — a stale cache from a
        previous root would show the wrong repos, so any mismatch (including a
        missing/corrupt cache) is treated the same as "no cache".
        """
        cached = load_workspace()
        if cached is None:
            return None
        if cached.root_path.resolve() != Path(folder).resolve():
            return None
        return cached

    def _start_scan(
        self,
        folder: str,
        *,
        auto_refresh: bool = False,
        rebuild: bool = True,
        dirty_paths: set[Path] | None = None,
        force_full_rescan: bool = False,
    ) -> None:
        if auto_refresh:
            since_last_scan = time.monotonic() - self._last_scan_finished_at
            if since_last_scan < _MIN_AUTO_REFRESH_INTERVAL_SECONDS:
                applog.log(
                    f"Skipping auto-refresh scan: previous scan finished {since_last_scan:.1f}s "
                    f"ago (< {_MIN_AUTO_REFRESH_INTERVAL_SECONDS:.0f}s minimum)",
                    level=applog.LogLevel.DEBUG,
                )
                return
        applog.log(
            f"Starting scan of {folder}" + (" (auto-refresh)" if auto_refresh else ""),
            level=applog.LogLevel.INFO,
        )
        # Every actual scan launch gets its own generation; the worker's
        # signal connections below close over this value so its result
        # handlers can tell a superseded scan's output from the current
        # scan's (see _scan_generation).
        self._scan_generation += 1
        scan_generation = self._scan_generation
        self._scan_started_at = time.monotonic()
        if auto_refresh:
            self._current_scan_label = "auto-refresh"
        elif self._startup_scan_pending:
            self._current_scan_label = "startup"
        else:
            self._current_scan_label = "manual"
        self._startup_scan_pending = False
        self._incremental_scan = auto_refresh or not rebuild
        self._scan_in_progress = True
        if not self._incremental_scan:
            self.statusBar().showMessage("Scanning: Starting scan…")
            cached_workspace = (
                self._load_matching_cached_workspace(folder) if self._workspace is None else None
            )
            if cached_workspace is not None:
                applog.log(
                    f"Using cached workspace for {folder} while rescanning",
                    level=applog.LogLevel.INFO,
                )
                self._workspace = cached_workspace
                self._showing_stale_cache = True
                self._update_status_extra_label()
                self._refresh_display()
                applog.log(
                    f"First tree painted from cache in "
                    f"{(time.monotonic() - self._app_started_at) * 1000:.0f}ms since app start "
                    "(startup)",
                    level=applog.LogLevel.DEBUG,
                )
            else:
                self._workspace = Workspace(root_path=Path(folder), repositories=[])
                self._refresh_display()
            self._scan_refresh_timer.start()
        previous_pull_requests: dict[Path, tuple[PullRequestInfo | None, str]] | None = None
        if auto_refresh and self._workspace is not None:
            previous_pull_requests = {
                repo.path: (repo.pull_request, repo.branch_status.branch_name)
                for repo in self._workspace.repositories
            }
        active_profile = self._active_profile()
        worker = ScanWorker(
            Path(folder),
            include_ignored=self._include_ignored_action.isChecked(),
            github_client=self._github_client(),
            previous_pull_requests=previous_pull_requests,
            is_cancelled=self._shutdown_requested.is_set,
            profile_repo_names=set(active_profile.repo_names) if active_profile else None,
            include_unpushed_commits=self._include_unpushed_commits_action.isChecked(),
            dirty_paths=dirty_paths,
            force_full_rescan=force_full_rescan,
            service=self._scanner_service,
        )
        worker.signals.progress.connect(
            self._guard_worker_result(
                lambda message, gen=scan_generation: self._on_scan_progress(message, gen)
            )
        )
        worker.signals.repo_ready.connect(
            self._guard_worker_result(
                lambda repo, gen=scan_generation: self._on_repo_ready(repo, gen)
            )
        )
        worker.signals.dead_repo.connect(
            self._guard_worker_result(
                lambda repo_path, gen=scan_generation: self._on_dead_repo(repo_path, gen)
            )
        )
        worker.signals.workspace_ready.connect(
            self._guard_worker_result(
                lambda workspace, gen=scan_generation: self._on_workspace_ready(workspace, gen)
            )
        )
        worker.signals.error.connect(
            self._guard_worker_result(
                lambda message, gen=scan_generation: self._on_scan_error(message, gen)
            )
        )
        worker.signals.log_message.connect(self._guard_worker_result(self._on_scan_log_message))
        worker.signals.debug_message.connect(
            self._guard_worker_result(self._on_scan_debug_message)
        )
        start_worker(self._thread_pool, worker)

    def _on_scan_progress(self, message: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._scan_generation:
            return
        applog.log(message, level=applog.LogLevel.DEBUG)
        self.statusBar().showMessage(f"Scanning: {message}")

    def _on_scan_log_message(self, message: str) -> None:
        applog.log(message, level=applog.LogLevel.WARNING)

    def _on_scan_debug_message(self, message: str) -> None:
        applog.log(message, level=applog.LogLevel.DEBUG)

    def _on_repo_ready(self, repo: Repository, generation: int | None = None) -> None:
        # A superseded scan (see _scan_generation) must not merge its repos
        # into the workspace a newer scan is building -- silently drop it.
        if generation is not None and generation != self._scan_generation:
            return
        # Rebuilding the tree (expandAll + restore-collapsed-state) is O(current
        # repo count), so accumulating it here without refreshing keeps repo
        # arrival cheap; the periodic timer coalesces the actual tree rebuilds
        # instead of doing one per repo.
        # Incremental scans (auto-refresh, or manual refresh with an already-loaded
        # workspace) leave self._workspace (and the displayed tree) untouched until
        # the full result is ready, so nothing to accumulate here.
        if self._incremental_scan:
            self._tree_view.highlight_repo(repo.path)
            return
        if self._workspace is None:
            return
        # A non-incremental (startup/manual) scan starts from a cached
        # workspace that may already list this exact repo path, so this must
        # merge by path rather than blindly append: appending created two
        # Repository entries for the same path, and RepoTreeModel._partition
        # then infers each one is the other's parent (a path is trivially
        # relative_to itself), knocking both out of the top-level `roots`
        # list -- with every repo duplicated that way, the entire tree
        # renders as empty until the scan finishes and replaces the list.
        for index, existing in enumerate(self._workspace.repositories):
            if existing.path == repo.path:
                self._workspace.repositories[index] = repo
                return
        self._workspace.repositories.append(repo)

    def _on_dead_repo(self, repo_path: Path, generation: int | None = None) -> None:
        # A superseded scan's "dead repo" signal must not remove a repo the
        # current scan is still tracking (see _scan_generation).
        if generation is not None and generation != self._scan_generation:
            return
        # Companion to _on_repo_ready's merge-by-path logic (e3aac9b): the
        # merge exists so a scan that legitimately reports only a subset
        # (dirty-gated repos, a partial/in-progress scan, cache hits) doesn't
        # wipe out the rest of the tree. That means mere absence from a
        # scan's results must NEVER remove a repo here -- only a scanner
        # trigger of "dead" does, which WorkspaceScannerService only emits
        # once GitPython has positively confirmed the directory no longer
        # exists (git.exc.NoSuchPathError), never on a transient git error.
        if self._incremental_scan:
            return
        if self._workspace is None:
            return
        self._workspace.repositories = [
            repo for repo in self._workspace.repositories if repo.path != repo_path
        ]

    def _on_workspace_ready(self, workspace: Workspace, generation: int | None = None) -> None:
        # A scan superseded by a newer one (folder switched, or a
        # user-initiated refresh/toggle fired mid-scan) must not overwrite
        # the newer scan's workspace, on-disk cache, or _scan_in_progress
        # state with its now-stale result (see _scan_generation).
        if generation is not None and generation != self._scan_generation:
            applog.log(
                f"Discarding workspace from superseded scan (generation {generation})",
                level=applog.LogLevel.DEBUG,
            )
            return
        self._scan_refresh_timer.stop()
        self._scan_in_progress = False
        self._last_scan_finished_at = time.monotonic()
        elapsed_seconds = time.monotonic() - self._scan_started_at
        applog.log(
            f"Total scan {elapsed_seconds * 1000:.0f}ms ({self._current_scan_label})",
            level=applog.LogLevel.DEBUG,
        )
        self._workspace = workspace
        self._showing_stale_cache = False
        self._update_status_extra_label()
        self._refresh_display(
            preserve_tree=self._incremental_scan or self._tree_view.has_rows()
        )
        self._incremental_scan = False
        self._tree_view.clear_repo_highlights()
        if self._use_file_watcher_action.isChecked():
            self._refresh_watch_paths(workspace.repositories)
        # Cache the freshly scanned workspace so the next cold start can paint
        # the tree immediately instead of showing nothing for ~30s (D3).
        save_workspace(workspace)
        repo_count = len(workspace.repositories)
        change_count = sum(len(r.changes) for r in workspace.repositories)
        message = (
            f"Done — {repo_count} repositories, {change_count} changed files "
            f"({elapsed_seconds:.1f}s)"
        )
        applog.log(message, level=applog.LogLevel.INFO)
        # timeout=0 keeps this visible until the next status message (e.g. the
        # next "Scanning: ..." update) replaces it, instead of vanishing after
        # a fixed delay — total scan time should stay legible, not flash by.
        self.statusBar().showMessage(message, 0)

    def _on_scan_refresh_tick(self) -> None:
        # _refresh_display is keyword-only on preserve_tree, so the QTimer
        # can't connect to it directly with the right default -- this tick
        # must always preserve the tree (never clear()+rebuild), since
        # clearing destroys expansion/scroll state mid-scan and, combined
        # with a workspace that transiently has zero top-level repos (see
        # _on_repo_ready), would otherwise flash the tree empty ~100x/scan.
        self._refresh_display(preserve_tree=True)

    def _refresh_display(self, *, preserve_tree: bool = False) -> None:
        if self._workspace is None:
            return
        refresh_started_at = time.monotonic()
        rules_desc = ", ".join(f"{r.mode.value}:{r.text!r}" for r in self._folder_filter_rules)
        applog.log(
            f"Applying folder filter rules ({len(self._folder_filter_rules)}): [{rules_desc}]",
            level=applog.LogLevel.DEBUG,
        )
        display_workspace = filter_workspace(
            self._workspace,
            ignore_md_files=self._ignore_md_action.isChecked(),
            hide_repos_without_changes=self._hide_empty_repos_action.isChecked(),
            hide_changeless_worktrees=self._hide_changeless_worktrees_checkbox.isChecked(),
            folder_filter_rules=self._folder_filter_rules,
            max_age_minutes=self._time_filter_minutes,
            profile=self._active_profile(),
            on_log=lambda msg: applog.log(msg, level=applog.LogLevel.DEBUG),
        )
        before_by_repo = {r.path: len(r.changes) for r in self._workspace.repositories}
        for repo in display_workspace.repositories:
            before_count = before_by_repo.get(repo.path, len(repo.changes))
            after_count = len(repo.changes)
            if before_count != after_count:
                applog.log(
                    f"Folder filter on {repo.path}: {before_count} -> {after_count} changes",
                    level=applog.LogLevel.DEBUG,
                )
        if preserve_tree:
            self._tree_view.update_workspace(display_workspace)
        else:
            self._tree_view.set_workspace(display_workspace)
        self._aggregate_list.set_workspace(display_workspace)
        change_count = sum(len(r.changes) for r in display_workspace.repositories)
        self._summary_label.setText(f"Total changed files: {change_count}")
        refresh_ms = (time.monotonic() - refresh_started_at) * 1000
        applog.log(
            f"_refresh_display took {refresh_ms:.0f}ms (preserve_tree={preserve_tree})",
            level=applog.LogLevel.DEBUG,
        )

    def _on_scan_error(self, message: str, generation: int | None = None) -> None:
        # See _scan_generation: an error from a scan a newer one already
        # superseded must not stop the newer scan's refresh timer or flip
        # _scan_in_progress off out from under it.
        if generation is not None and generation != self._scan_generation:
            return
        self._scan_refresh_timer.stop()
        self._scan_in_progress = False
        self._last_scan_finished_at = time.monotonic()
        self._report_error(f"Scan failed: {message}")
