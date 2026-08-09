import threading
import time
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QThreadPool, QTimer, QUrl
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
    QFileDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QTabWidget,
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
from local_changes_viewer.core.services.workspace_cache import load_workspace, save_workspace
from local_changes_viewer.core.services.workspace_filter import filter_workspace
from local_changes_viewer.core.services.workspace_scanner_service import (
    WorkspaceScannerService,
)
from local_changes_viewer.gui import applog, github_auth
from local_changes_viewer.gui.commit_log_dialog import CommitLogDialog
from local_changes_viewer.gui.diff_view.diff_view_widget import DiffViewWidget
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
from local_changes_viewer.gui.profile_dialog import ProfileDialog
from local_changes_viewer.gui.pull_requests_panel import PullRequestsPanel
from local_changes_viewer.gui.pull_request_info_dialog import PullRequestInfoDialog
from local_changes_viewer.gui.pull_request_issues_dialog import PullRequestIssuesDialog
from local_changes_viewer.gui.settings import AppSettings
from local_changes_viewer.gui.workers.diff_worker import DiffWorker
from local_changes_viewer.gui.workers.my_pull_requests_worker import MyPullRequestsWorker
from local_changes_viewer.gui.workers.pull_request_details_worker import PullRequestDetailsWorker
from local_changes_viewer.gui.workers.pull_request_refresh_worker import PullRequestRefreshWorker
from local_changes_viewer.gui.workers.pull_request_threads_worker import PullRequestThreadsWorker
from local_changes_viewer.gui.workers.repo_refresh_worker import RepoRefreshWorker
from local_changes_viewer.gui.workers.scan_worker import ScanWorker
from local_changes_viewer.gui.workers.watch_paths_worker import WatchPathsWorker
from local_changes_viewer.gui.workspace_watcher import WorkspaceFileWatcher
from local_changes_viewer.gui.workspace_tree.aggregate_list import AggregateChangeList
from local_changes_viewer.gui.workspace_tree.tree_model import (
    FILE_CHANGE_ROLE,
    FOLDER_PATH_ROLE,
    NODE_KEY_ROLE,
)
from local_changes_viewer.gui.workspace_tree.tree_view import RepoTreeView

# A busy workspace (many repos + a fast file watcher) can fire an auto-refresh
# scan every couple of seconds; if the previous scan just finished, skip this
# one rather than piling another full 27-repo git+GitHub scan on top of it.
_MIN_AUTO_REFRESH_INTERVAL_SECONDS = 5.0


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
        self._thread_pool = QThreadPool.globalInstance()
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
        self._tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree_view.customContextMenuRequested.connect(self._on_tree_context_menu)
        self._filter_box = QLineEdit()
        self._filter_box.setPlaceholderText("Filter by path…")
        self._filter_box.textChanged.connect(self._tree_view.set_filter_text)
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
        tree_layout.addWidget(self._filter_box)

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
        self._settings.set_sync_side_by_side_scroll(self._sync_scroll_action.isChecked())
        self._settings.set_always_reload_diff(self._always_reload_diff_action.isChecked())
        super().closeEvent(event)

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
            self._start_scan(self._root_folder)

    def _on_include_unpushed_commits_toggled(self, checked: bool) -> None:
        applog.log(f"Show committed but not pushed files: {checked}", level=applog.LogLevel.INFO)
        if self._restoring_settings:
            return
        if self._root_folder:
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
        if self._diff_view.discard_edits_if_any():
            self.statusBar().showMessage("Discarded unsaved edits", 5000)
        self._selected_change = change
        self._selected_repo_path = repo_path
        self._update_file_info_label(repo_path, change)
        if change.diff is not None and not self._always_reload_diff_action.isChecked():
            self._diff_view.set_diff(change.diff, str(change.path), self._editable_path(repo_path, change))
            return
        self._load_diff(repo_path, change)

    def _editable_path(self, repo_path: Path, change: FileChange) -> Path | None:
        if change.change_type == ChangeType.DELETED or change.is_directory:
            return None
        return repo_path / change.path

    def _update_file_info_label(self, repo_path: Path, change: FileChange) -> None:
        if change.change_type == ChangeType.DELETED:
            self._file_info_label.setText("Deleted")
            return
        try:
            content = (repo_path / change.path).read_bytes()
        except OSError:
            self._file_info_label.setText("")
            return
        encoding = detect_encoding(content)
        line_ending = detect_line_ending(content)
        self._file_info_label.setText(f"{encoding} · {line_ending}")

    def _load_diff(self, repo_path: Path, change: FileChange) -> None:
        self._diff_view.clear_diff()
        worker = DiffWorker(
            repo_path, change, ignore_whitespace=self._ignore_whitespace_action.isChecked()
        )
        worker.signals.diff_ready.connect(self._on_diff_ready)
        worker.signals.error.connect(self._on_diff_error)
        self._thread_pool.start(worker)

    def _on_diff_ready(self, change: FileChange, diff) -> None:
        change.diff = diff
        if change is self._selected_change and self._selected_repo_path is not None:
            abs_path = self._editable_path(self._selected_repo_path, change)
            self._diff_view.set_diff(diff, str(change.path), abs_path)

    def _on_diff_error(self, message: str) -> None:
        applog.log(f"Diff failed: {message}", level=applog.LogLevel.ERROR)
        self.statusBar().showMessage(f"Diff failed: {message}", 5000)

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
        worker.signals.finished.connect(self._file_watcher.set_watch_paths)
        self._thread_pool.start(worker)
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
        levels = [level.name for level in applog.LogLevel]
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
        worker.signals.finished.connect(self._on_my_pull_requests_ready)
        worker.signals.error.connect(self._on_my_pull_requests_error)
        worker.signals.progress.connect(self._on_scan_progress)
        self._thread_pool.start(worker)
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
        worker.signals.finished.connect(self._on_pull_request_refresh_ready)
        worker.signals.error.connect(self._on_pull_request_action_error)
        self._thread_pool.start(worker)

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
        worker.signals.finished.connect(self._on_pull_request_details_ready)
        worker.signals.error.connect(self._on_pull_request_action_error)
        self._thread_pool.start(worker)

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
        worker.signals.finished.connect(self._on_pull_request_threads_ready)
        worker.signals.error.connect(self._on_pull_request_action_error)
        self._thread_pool.start(worker)

    def _on_pull_request_threads_ready(self, number: int, threads: list) -> None:
        self.statusBar().clearMessage()
        PullRequestIssuesDialog(threads, number, self).exec()

    def _on_pull_request_action_error(self, message: str) -> None:
        applog.log(f"Pull request action failed: {message}", level=applog.LogLevel.ERROR)
        self.statusBar().clearMessage()
        QMessageBox.warning(self, "Pull Request", f"Action failed: {message}")

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

    def _on_refresh_repo(self, repo_path: Path) -> None:
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
            lambda repo: self._on_repo_refreshed(repo_path, repo)
        )
        worker.signals.error.connect(self._on_scan_error)
        worker.signals.log_message.connect(self._on_scan_log_message)
        self._thread_pool.start(worker)

    def _on_repo_refreshed(self, repo_path: Path, repo: Repository | None) -> None:
        if self._workspace is None:
            return
        if repo is None:
            self.statusBar().showMessage(f"Failed to refresh {repo_path.name}", 5000)
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
        worker.signals.progress.connect(self._on_scan_progress)
        worker.signals.repo_ready.connect(self._on_repo_ready)
        worker.signals.workspace_ready.connect(self._on_workspace_ready)
        worker.signals.error.connect(self._on_scan_error)
        worker.signals.log_message.connect(self._on_scan_log_message)
        worker.signals.debug_message.connect(self._on_scan_debug_message)
        self._thread_pool.start(worker)

    def _on_scan_progress(self, message: str) -> None:
        applog.log(message, level=applog.LogLevel.DEBUG)
        self.statusBar().showMessage(f"Scanning: {message}")

    def _on_scan_log_message(self, message: str) -> None:
        applog.log(message, level=applog.LogLevel.WARNING)

    def _on_scan_debug_message(self, message: str) -> None:
        applog.log(message, level=applog.LogLevel.DEBUG)

    def _on_repo_ready(self, repo: Repository) -> None:
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

    def _on_workspace_ready(self, workspace: Workspace) -> None:
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
            folder_filter_rules=self._folder_filter_rules,
            max_age_minutes=self._time_filter_minutes,
            profile=self._active_profile(),
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

    def _on_scan_error(self, message: str) -> None:
        self._scan_refresh_timer.stop()
        self._scan_in_progress = False
        self._last_scan_finished_at = time.monotonic()
        applog.log(f"Scan failed: {message}", level=applog.LogLevel.ERROR)
        self.statusBar().showMessage(f"Scan failed: {message}", 5000)
