from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QGuiApplication,
    QKeySequence,
)
from PySide6.QtWidgets import (
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
from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterRule
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
from local_changes_viewer.core.services.workspace_filter import filter_workspace
from local_changes_viewer.gui import applog, github_auth
from local_changes_viewer.gui.diff_view.diff_view_widget import DiffViewWidget
from local_changes_viewer.gui.folder_filter_dialog import FolderFilterDialog
from local_changes_viewer.gui.github_connect_dialog import GitHubConnectDialog
from local_changes_viewer.gui.my_pull_requests_dialog import MyPullRequestsDialog
from local_changes_viewer.gui.pull_request_info_dialog import PullRequestInfoDialog
from local_changes_viewer.gui.pull_request_issues_dialog import PullRequestIssuesDialog
from local_changes_viewer.gui.settings import AppSettings
from local_changes_viewer.gui.workers.diff_worker import DiffWorker
from local_changes_viewer.gui.workers.my_pull_requests_worker import MyPullRequestsWorker
from local_changes_viewer.gui.workers.pull_request_details_worker import PullRequestDetailsWorker
from local_changes_viewer.gui.workers.pull_request_refresh_worker import PullRequestRefreshWorker
from local_changes_viewer.gui.workers.pull_request_threads_worker import PullRequestThreadsWorker
from local_changes_viewer.gui.workers.scan_worker import ScanWorker
from local_changes_viewer.gui.workspace_tree.aggregate_list import AggregateChangeList
from local_changes_viewer.gui.workspace_tree.tree_model import FILE_CHANGE_ROLE
from local_changes_viewer.gui.workspace_tree.tree_view import RepoTreeView


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("local-changes-viewer")
        self.resize(1200, 800)

        self._settings = AppSettings()
        applog.set_level(applog.level_from_name(self._settings.log_level()))
        self._root_folder: str | None = None
        self._workspace: Workspace | None = None
        self._folder_filter_rules: list[FolderFilterRule] = self._settings.folder_filter_rules()
        self._selected_change: FileChange | None = None
        self._selected_repo_path: Path | None = None
        self._thread_pool = QThreadPool.globalInstance()
        self._scan_refresh_timer = QTimer(self)
        self._scan_refresh_timer.setInterval(150)
        self._scan_refresh_timer.timeout.connect(self._refresh_display)
        self._incremental_scan = False
        self._auto_refresh_minutes = 0
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.timeout.connect(self._on_auto_refresh_timeout)
        self._my_pull_requests_dialog: MyPullRequestsDialog | None = None

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
        tree_layout.addWidget(left_tabs)

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

        increase_font_action = QAction("Increase Font Size", self)
        increase_font_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        increase_font_action.triggered.connect(self._diff_view.increase_font_size)
        view_menu.addAction(increase_font_action)

        decrease_font_action = QAction("Decrease Font Size", self)
        decrease_font_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        decrease_font_action.triggered.connect(self._diff_view.decrease_font_size)
        view_menu.addAction(decrease_font_action)

        settings_menu = self.menuBar().addMenu("Settings")

        self._include_ignored_action = QAction("Show ignored files", self, checkable=True)
        self._include_ignored_action.toggled.connect(self._on_include_ignored_toggled)
        settings_menu.addAction(self._include_ignored_action)

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

        log_level_action = QAction("Log Level…", self)
        log_level_action.triggered.connect(self._on_configure_log_level)
        settings_menu.addAction(log_level_action)

        manage_folder_filters_action = QAction("Filtered Folders…", self)
        manage_folder_filters_action.triggered.connect(self._on_manage_folder_filters)
        settings_menu.addAction(manage_folder_filters_action)

        github_menu = self.menuBar().addMenu("GitHub")

        my_pull_requests_action = QAction("My Open Pull Requests…", self)
        my_pull_requests_action.triggered.connect(self._on_show_my_pull_requests)
        github_menu.addAction(my_pull_requests_action)

        github_menu.addSeparator()

        connect_github_action = QAction("Connect to GitHub…", self)
        connect_github_action.triggered.connect(self._on_connect_github)
        github_menu.addAction(connect_github_action)

        self._disconnect_github_action = QAction("Disconnect GitHub", self)
        self._disconnect_github_action.triggered.connect(self._on_disconnect_github)
        github_menu.addAction(self._disconnect_github_action)

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
        self._restore_window_state()
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
        self._ignore_md_action.setChecked(self._settings.ignore_md_files())
        self._hide_empty_repos_action.setChecked(self._settings.hide_repos_without_changes())
        self._sync_scroll_action.setChecked(self._settings.sync_side_by_side_scroll())
        self._diff_view.set_sync_scroll(self._sync_scroll_action.isChecked())
        self._always_reload_diff_action.setChecked(self._settings.always_reload_diff())
        self._apply_auto_refresh_interval(self._settings.auto_refresh_minutes())
        self._disconnect_github_action.setEnabled(self._settings.github_username() is not None)

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
        self._thread_pool.clear()
        self._thread_pool.waitForDone()
        self._settings.set_window_geometry(self.saveGeometry())
        self._settings.set_splitter_sizes(self._splitter.sizes())
        mode = "side_by_side" if self._diff_view.is_side_by_side() else "unified"
        self._settings.set_diff_view_mode(mode)
        self._settings.set_ignore_whitespace(self._ignore_whitespace_action.isChecked())
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
        if self._root_folder:
            self._start_scan(self._root_folder)

    def _on_refresh(self) -> None:
        applog.log("Refresh", level=applog.LogLevel.INFO)
        if self._root_folder:
            self._start_scan(self._root_folder, rebuild=self._workspace is None)

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
        if self._auto_refresh_minutes:
            parts.append(f"Auto refresh: {self._auto_refresh_minutes} min")
        if self._time_filter_minutes:
            parts.append(f"Last commit: {self._time_filter_minutes} min")
        self._status_extra_label.setText("  |  ".join(parts))

    def _on_auto_refresh_timeout(self) -> None:
        if self._root_folder:
            self._start_scan(self._root_folder, auto_refresh=True)

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
        self._fetch_my_pull_requests()

    def _on_my_pull_requests_refresh_requested(self) -> None:
        if self._my_pull_requests_dialog is not None:
            self._my_pull_requests_dialog.set_refreshing(True)
        self._fetch_my_pull_requests()

    def _fetch_my_pull_requests(self) -> None:
        username = self._settings.github_username()
        github_client = self._github_client()
        if username is None or github_client is None:
            QMessageBox.information(
                self, "My Open Pull Requests", "Connect to GitHub first (Settings menu)."
            )
            return
        if self._workspace is None or not self._workspace.repositories:
            QMessageBox.information(self, "My Open Pull Requests", "No repositories loaded.")
            return

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
            return

        applog.log("My Open Pull Requests", level=applog.LogLevel.INFO)
        self.statusBar().showMessage("Fetching your open pull requests…")
        worker = MyPullRequestsWorker(github_client, username, owner_repo_pairs)
        worker.signals.finished.connect(self._on_my_pull_requests_ready)
        worker.signals.error.connect(self._on_my_pull_requests_error)
        worker.signals.progress.connect(self._on_scan_progress)
        self._thread_pool.start(worker)

    def _on_my_pull_requests_ready(self, pull_requests: list) -> None:
        self.statusBar().clearMessage()
        if self._my_pull_requests_dialog is not None:
            self._my_pull_requests_dialog.set_pull_requests(pull_requests)
            self._my_pull_requests_dialog.set_refreshing(False)
            return

        dialog = MyPullRequestsDialog(pull_requests, self)
        dialog.refresh_requested.connect(self._on_my_pull_requests_refresh_requested)
        dialog.pull_request_refresh_requested.connect(self._on_pull_request_refresh_requested)
        dialog.pull_request_info_requested.connect(self._on_pull_request_info_requested)
        dialog.pull_request_issues_requested.connect(self._on_pull_request_issues_requested)
        self._my_pull_requests_dialog = dialog
        dialog.exec()
        self._my_pull_requests_dialog = None

    def _on_my_pull_requests_error(self, message: str) -> None:
        applog.log(f"Failed to fetch open pull requests: {message}", level=applog.LogLevel.ERROR)
        self.statusBar().clearMessage()
        if self._my_pull_requests_dialog is not None:
            self._my_pull_requests_dialog.set_refreshing(False)
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
        if not index.isValid() or index.data(FILE_CHANGE_ROLE) is None:
            return
        self._tree_view.setCurrentIndex(index)

        menu = QMenu(self._tree_view)
        menu.addAction("Copy Path", self._on_copy_file_path)
        menu.addAction("Copy Name", self._on_copy_file_name)
        menu.addAction("Refresh Diff", self._on_refresh_diff)
        menu.exec(self._tree_view.viewport().mapToGlobal(pos))

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

    def _start_scan(self, folder: str, *, auto_refresh: bool = False, rebuild: bool = True) -> None:
        applog.log(
            f"Starting scan of {folder}" + (" (auto-refresh)" if auto_refresh else ""),
            level=applog.LogLevel.INFO,
        )
        self._incremental_scan = auto_refresh or not rebuild
        if not self._incremental_scan:
            self.statusBar().showMessage("Starting scan…")
            self._workspace = Workspace(root_path=Path(folder), repositories=[])
            self._refresh_display()
            self._scan_refresh_timer.start()
        previous_pull_requests: dict[Path, tuple[PullRequestInfo, str]] | None = None
        if auto_refresh and self._workspace is not None:
            previous_pull_requests = {
                repo.path: (repo.pull_request, repo.branch_status.branch_name)
                for repo in self._workspace.repositories
                if repo.pull_request is not None
            }
        worker = ScanWorker(
            Path(folder),
            include_ignored=self._include_ignored_action.isChecked(),
            github_client=self._github_client(),
            previous_pull_requests=previous_pull_requests,
        )
        worker.signals.progress.connect(self._on_scan_progress)
        worker.signals.repo_ready.connect(self._on_repo_ready)
        worker.signals.workspace_ready.connect(self._on_workspace_ready)
        worker.signals.error.connect(self._on_scan_error)
        worker.signals.log_message.connect(self._on_scan_log_message)
        self._thread_pool.start(worker)

    def _on_scan_progress(self, message: str) -> None:
        applog.log(message, level=applog.LogLevel.DEBUG)
        self.statusBar().showMessage(message)

    def _on_scan_log_message(self, message: str) -> None:
        applog.log(message, level=applog.LogLevel.WARNING)

    def _on_repo_ready(self, repo: Repository) -> None:
        # Rebuilding the tree (expandAll + restore-collapsed-state) is O(current
        # repo count), so appending it here without refreshing keeps repo arrival
        # cheap; the periodic timer coalesces the actual tree rebuilds instead of
        # doing one per repo.
        # Incremental scans (auto-refresh, or manual refresh with an already-loaded
        # workspace) leave self._workspace (and the displayed tree) untouched until
        # the full result is ready, so nothing to accumulate here.
        if self._incremental_scan:
            self._tree_view.highlight_repo(repo.path)
            return
        if self._workspace is not None:
            self._workspace.repositories.append(repo)

    def _on_workspace_ready(self, workspace: Workspace) -> None:
        self._scan_refresh_timer.stop()
        self._workspace = workspace
        self._refresh_display(preserve_tree=self._incremental_scan)
        self._incremental_scan = False
        self._tree_view.clear_repo_highlights()
        repo_count = len(workspace.repositories)
        change_count = sum(len(r.changes) for r in workspace.repositories)
        message = f"Done — {repo_count} repositories, {change_count} changed files"
        applog.log(message, level=applog.LogLevel.INFO)
        self.statusBar().showMessage(message, 5000)

    def _refresh_display(self, *, preserve_tree: bool = False) -> None:
        if self._workspace is None:
            return
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

    def _on_scan_error(self, message: str) -> None:
        self._scan_refresh_timer.stop()
        applog.log(f"Scan failed: {message}", level=applog.LogLevel.ERROR)
        self.statusBar().showMessage(f"Scan failed: {message}", 5000)
