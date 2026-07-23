from pathlib import Path

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from local_changes_viewer.core.domain.file_change import FileChange
from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.core.services.diff_formatting import format_unified_diff
from local_changes_viewer.gui import applog
from local_changes_viewer.gui.diff_view.diff_view_widget import DiffViewWidget
from local_changes_viewer.gui.settings import AppSettings
from local_changes_viewer.gui.workers.diff_worker import DiffWorker
from local_changes_viewer.gui.workers.scan_worker import ScanWorker
from local_changes_viewer.gui.workspace_tree.tree_view import RepoTreeView


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("local-changes-viewer")
        self.resize(1200, 800)

        self._settings = AppSettings()
        self._root_folder: str | None = None
        self._workspace: Workspace | None = None
        self._selected_change: FileChange | None = None
        self._selected_repo_path: Path | None = None
        self._thread_pool = QThreadPool.globalInstance()

        self._tree_view = RepoTreeView(self._settings)
        self._tree_view.file_selected.connect(self._on_file_selected)
        self._filter_box = QLineEdit()
        self._filter_box.setPlaceholderText("Filter by path…")
        self._filter_box.textChanged.connect(self._tree_view.set_filter_text)
        self._diff_view = DiffViewWidget()

        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.addWidget(self._filter_box)
        tree_layout.addWidget(self._tree_view)

        splitter = QSplitter()
        splitter.addWidget(tree_panel)
        splitter.addWidget(self._diff_view)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)

        toolbar = QToolBar("Main")
        self.addToolBar(toolbar)

        open_action = QAction("Open Folder…", self)
        open_action.triggered.connect(self._on_open_folder)
        toolbar.addAction(open_action)

        self._include_ignored_checkbox = QCheckBox("Show ignored files")
        self._include_ignored_checkbox.toggled.connect(self._on_include_ignored_toggled)
        toolbar.addWidget(self._include_ignored_checkbox)

        self._ignore_whitespace_checkbox = QCheckBox("Ignore whitespace")
        self._ignore_whitespace_checkbox.toggled.connect(self._on_ignore_whitespace_toggled)
        toolbar.addWidget(self._ignore_whitespace_checkbox)

        collapse_all_action = QAction("Collapse All", self)
        collapse_all_action.triggered.connect(self._tree_view.collapse_all)
        toolbar.addAction(collapse_all_action)

        expand_all_action = QAction("Expand All", self)
        expand_all_action.triggered.connect(self._tree_view.expand_all)
        toolbar.addAction(expand_all_action)

        app_log_action = QAction("App Log", self)
        app_log_action.triggered.connect(self._on_copy_app_log)
        toolbar.addAction(app_log_action)

        copy_diff_action = QAction("Copy Diff", self)
        copy_diff_action.triggered.connect(self._on_copy_diff)
        toolbar.addAction(copy_diff_action)

        copy_path_action = QAction("Copy File Path", self)
        copy_path_action.triggered.connect(self._on_copy_file_path)
        toolbar.addAction(copy_path_action)

        refresh_action = QAction("Refresh", self)
        refresh_action.triggered.connect(self._on_refresh)
        toolbar.addAction(refresh_action)

        self._folder_status_label = QLabel("No folder open")
        self.statusBar().addPermanentWidget(self._folder_status_label)

        self._restore_last_folder()

    def _restore_last_folder(self) -> None:
        last_folder = self._settings.last_root_folder()
        if last_folder:
            self._set_root_folder(last_folder)

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
        if change.diff is not None:
            self._diff_view.set_diff(change.diff, str(change.path))
            return
        self._load_diff(repo_path, change)

    def _load_diff(self, repo_path: Path, change: FileChange) -> None:
        self._diff_view.clear_diff()
        worker = DiffWorker(
            repo_path, change, ignore_whitespace=self._ignore_whitespace_checkbox.isChecked()
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

    def _set_root_folder(self, folder: str) -> None:
        self._root_folder = folder
        self._settings.set_last_root_folder(folder)
        self._folder_status_label.setText(f"Folder: {folder}")
        self._start_scan(folder)

    def _start_scan(self, folder: str) -> None:
        self.statusBar().showMessage("Scanning...")
        worker = ScanWorker(
            Path(folder), include_ignored=self._include_ignored_checkbox.isChecked()
        )
        worker.signals.workspace_ready.connect(self._on_workspace_ready)
        worker.signals.error.connect(self._on_scan_error)
        self._thread_pool.start(worker)

    def _on_workspace_ready(self, workspace: Workspace) -> None:
        self._workspace = workspace
        self._tree_view.set_workspace(workspace)
        repo_count = len(workspace.repositories)
        change_count = sum(len(r.changes) for r in workspace.repositories)
        self.statusBar().showMessage(
            f"Done — {repo_count} repositories, {change_count} changed files", 5000
        )

    def _on_scan_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Scan failed: {message}", 5000)
