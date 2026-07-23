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

from local_changes_viewer.core.domain.workspace import Workspace
from local_changes_viewer.gui import applog
from local_changes_viewer.gui.settings import AppSettings
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
        self._thread_pool = QThreadPool.globalInstance()

        self._tree_view = RepoTreeView(self._settings)
        self._filter_box = QLineEdit()
        self._filter_box.setPlaceholderText("Filter by path…")
        self._filter_box.textChanged.connect(self._tree_view.set_filter_text)
        self._diff_placeholder = QLabel("Select a file to view its diff")

        tree_panel = QWidget()
        tree_layout = QVBoxLayout(tree_panel)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.addWidget(self._filter_box)
        tree_layout.addWidget(self._tree_view)

        splitter = QSplitter()
        splitter.addWidget(tree_panel)
        splitter.addWidget(self._diff_placeholder)
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

        collapse_all_action = QAction("Collapse All", self)
        collapse_all_action.triggered.connect(self._tree_view.collapse_all)
        toolbar.addAction(collapse_all_action)

        expand_all_action = QAction("Expand All", self)
        expand_all_action.triggered.connect(self._tree_view.expand_all)
        toolbar.addAction(expand_all_action)

        app_log_action = QAction("App Log", self)
        app_log_action.triggered.connect(self._on_copy_app_log)
        toolbar.addAction(app_log_action)

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

    def _on_copy_app_log(self) -> None:
        text = "\n".join(applog.all_entries())
        QGuiApplication.clipboard().setText(text)
        self.statusBar().showMessage("App log copied to clipboard", 3000)

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
