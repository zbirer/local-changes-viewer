from pathlib import Path

from PySide6.QtCore import QProcess, QThreadPool, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterRule
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.services.diff_formatting import format_unified_diff
from local_changes_viewer.core.services.file_info import detect_encoding, detect_line_ending
from local_changes_viewer.core.services.workspace_filter import filter_workspace
from local_changes_viewer.gui import applog
from local_changes_viewer.gui.diff_view.diff_view_widget import DiffViewWidget
from local_changes_viewer.gui.folder_filter_dialog import FolderFilterDialog
from local_changes_viewer.gui.settings import AppSettings
from local_changes_viewer.gui.workers.diff_worker import DiffWorker
from local_changes_viewer.gui.workers.scan_worker import ScanWorker
from local_changes_viewer.gui.workspace_tree.aggregate_list import AggregateChangeList
from local_changes_viewer.gui.workspace_tree.tree_view import RepoTreeView


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("local-changes-viewer")
        self.resize(1200, 800)

        self._settings = AppSettings()
        self._root_folder: str | None = None
        self._workspace: Workspace | None = None
        self._folder_filter_rules: list[FolderFilterRule] = self._settings.folder_filter_rules()
        self._selected_change: FileChange | None = None
        self._selected_repo_path: Path | None = None
        self._thread_pool = QThreadPool.globalInstance()

        self._tree_view = RepoTreeView(self._settings)
        self._tree_view.file_selected.connect(self._on_file_selected)
        self._filter_box = QLineEdit()
        self._filter_box.setPlaceholderText("Filter by path…")
        self._filter_box.textChanged.connect(self._tree_view.set_filter_text)
        self._diff_view = DiffViewWidget()

        self._aggregate_list = AggregateChangeList()
        self._aggregate_list.file_selected.connect(self._on_file_selected)
        self._tree_view.scope_changed.connect(self._aggregate_list.set_scope)

        left_tabs = QTabWidget()
        left_tabs.addTab(self._tree_view, "Folder Tree")
        left_tabs.addTab(self._aggregate_list, "All Changes")

        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
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

        manage_folder_filters_action = QAction("Manage Folder Filters…", self)
        manage_folder_filters_action.triggered.connect(self._on_manage_folder_filters)
        settings_menu.addAction(manage_folder_filters_action)

        actions_menu.addSeparator()

        collapse_all_action = QAction("Collapse All", self)
        collapse_all_action.triggered.connect(self._tree_view.collapse_all)
        actions_menu.addAction(collapse_all_action)

        expand_all_action = QAction("Expand All", self)
        expand_all_action.triggered.connect(self._tree_view.expand_all)
        actions_menu.addAction(expand_all_action)

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

        open_editor_action = QAction("Open in Default Editor", self)
        open_editor_action.triggered.connect(self._on_open_in_editor)
        actions_menu.addAction(open_editor_action)

        reveal_action = QAction("Reveal in Finder", self)
        reveal_action.triggered.connect(self._on_reveal_in_finder)
        actions_menu.addAction(reveal_action)

        actions_menu.addSeparator()

        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self._on_refresh)
        actions_menu.addAction(refresh_action)

        self._folder_status_label = QLabel("No folder open")
        self.statusBar().addPermanentWidget(self._folder_status_label)

        self._summary_label = QLabel("")
        self.statusBar().addPermanentWidget(self._summary_label)

        self._file_info_label = QLabel("")
        self.statusBar().addPermanentWidget(self._file_info_label)

        self._restore_last_folder()
        self._restore_window_state()

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

    def closeEvent(self, event: QCloseEvent) -> None:
        self._settings.set_window_geometry(self.saveGeometry())
        self._settings.set_splitter_sizes(self._splitter.sizes())
        mode = "side_by_side" if self._diff_view.is_side_by_side() else "unified"
        self._settings.set_diff_view_mode(mode)
        self._settings.set_ignore_whitespace(self._ignore_whitespace_action.isChecked())
        self._settings.set_ignore_md_files(self._ignore_md_action.isChecked())
        self._settings.set_hide_repos_without_changes(self._hide_empty_repos_action.isChecked())
        super().closeEvent(event)

    def _on_open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open Folder")
        if folder:
            self._set_root_folder(folder)

    def _on_include_ignored_toggled(self, _checked: bool) -> None:
        if self._root_folder:
            self._start_scan(self._root_folder)

    def _on_refresh(self) -> None:
        if self._root_folder:
            self._start_scan(self._root_folder)

    def _on_file_selected(self, repo_path: Path, change: FileChange) -> None:
        self._selected_change = change
        self._selected_repo_path = repo_path
        self._update_file_info_label(repo_path, change)
        if change.diff is not None:
            self._diff_view.set_diff(change.diff, str(change.path))
            return
        self._load_diff(repo_path, change)

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
        if change is self._selected_change:
            self._diff_view.set_diff(diff, str(change.path))

    def _on_diff_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Diff failed: {message}", 5000)

    def _on_ignore_whitespace_toggled(self, _checked: bool) -> None:
        if self._workspace is not None:
            for repo in self._workspace.repositories:
                for change in repo.changes:
                    change.diff = None
        if self._selected_change is not None and self._selected_repo_path is not None:
            self._load_diff(self._selected_repo_path, self._selected_change)

    def _on_display_filter_toggled(self, _checked: bool) -> None:
        self._refresh_display()

    def _on_manage_folder_filters(self) -> None:
        dialog = FolderFilterDialog(self._folder_filter_rules, self)
        dialog.rules_changed.connect(self._on_folder_filter_rules_changed)
        dialog.exec()

    def _on_folder_filter_rules_changed(self, rules: list[FolderFilterRule]) -> None:
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

    def _start_scan(self, folder: str) -> None:
        self.statusBar().showMessage("Scanning...")
        worker = ScanWorker(
            Path(folder), include_ignored=self._include_ignored_action.isChecked()
        )
        worker.signals.workspace_ready.connect(self._on_workspace_ready)
        worker.signals.error.connect(self._on_scan_error)
        self._thread_pool.start(worker)

    def _on_workspace_ready(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._refresh_display()
        repo_count = len(workspace.repositories)
        change_count = sum(len(r.changes) for r in workspace.repositories)
        self.statusBar().showMessage(
            f"Done — {repo_count} repositories, {change_count} changed files", 5000
        )

    def _refresh_display(self) -> None:
        if self._workspace is None:
            return
        display_workspace = filter_workspace(
            self._workspace,
            ignore_md_files=self._ignore_md_action.isChecked(),
            hide_repos_without_changes=self._hide_empty_repos_action.isChecked(),
            folder_filter_rules=self._folder_filter_rules,
        )
        self._tree_view.set_workspace(display_workspace)
        self._aggregate_list.set_workspace(display_workspace)
        change_count = sum(len(r.changes) for r in display_workspace.repositories)
        self._summary_label.setText(f"Total changed files: {change_count}")

    def _on_scan_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Scan failed: {message}", 5000)
