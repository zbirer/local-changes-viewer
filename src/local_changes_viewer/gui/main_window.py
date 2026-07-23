from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFileDialog, QLabel, QMainWindow, QToolBar

from local_changes_viewer.gui.settings import AppSettings


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("local-changes-viewer")
        self.resize(1200, 800)

        self._settings = AppSettings()
        self._root_folder: str | None = None

        self._folder_label = QLabel("No folder open")
        self.setCentralWidget(self._folder_label)

        toolbar = QToolBar("Main")
        self.addToolBar(toolbar)

        open_action = QAction("Open Folder…", self)
        open_action.triggered.connect(self._on_open_folder)
        toolbar.addAction(open_action)

        self._restore_last_folder()

    def _restore_last_folder(self) -> None:
        last_folder = self._settings.last_root_folder()
        if last_folder:
            self._set_root_folder(last_folder)

    def _on_open_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Open Folder")
        if folder:
            self._set_root_folder(folder)

    def _set_root_folder(self, folder: str) -> None:
        self._root_folder = folder
        self._settings.set_last_root_folder(folder)
        self._folder_label.setText(f"Folder: {folder}")
