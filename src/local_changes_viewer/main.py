import sys

from PySide6.QtWidgets import QApplication

from local_changes_viewer.gui.app_icon import build_app_icon
from local_changes_viewer.gui.main_window import MainWindow

APP_NAME = "GitChanges"


def _disable_macos_fullscreen_menu_item() -> None:
    # macOS auto-injects "Enter Full Screen" into any menu titled "View"; the
    # only reliable way to suppress it is this NSUserDefaults key.
    if sys.platform != "darwin":
        return
    try:
        from Foundation import NSUserDefaults
    except ImportError:
        return
    NSUserDefaults.standardUserDefaults().setBool_forKey_(False, "NSFullScreenMenuItemEverywhere")


def main() -> int:
    _disable_macos_fullscreen_menu_item()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setWindowIcon(build_app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
