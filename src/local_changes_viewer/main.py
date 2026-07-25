import sys

from PySide6.QtWidgets import QApplication

from local_changes_viewer.gui.app_icon import build_app_icon
from local_changes_viewer.gui.main_window import MainWindow

APP_NAME = "GitChanges"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(build_app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
