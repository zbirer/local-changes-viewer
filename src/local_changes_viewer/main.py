import faulthandler
import sys
import traceback

from PySide6.QtWidgets import QApplication

from local_changes_viewer.gui import applog
from local_changes_viewer.gui.app_icon import build_app_icon
from local_changes_viewer.gui.main_window import MainWindow

APP_NAME = "GitChanges"

CRASH_LOG_PATH = applog.LOG_FILE_PATH.parent / "crash.log"


def _enable_crash_diagnostics() -> None:
    # A segfault happens below the Python interpreter (typically a native
    # Qt/PySide bug) and can't be caught with try/except -- the OS crash
    # reporter shows no Python frames at all, so faulthandler is the closest
    # thing available: on a fatal signal it dumps the Python stack of the
    # crashing thread to this file, which is otherwise the only trace left.
    faulthandler.enable(file=CRASH_LOG_PATH.open("a"), all_threads=True)

    def _log_uncaught_exception(exc_type, exc_value, exc_tb) -> None:
        message = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        applog.log(f"Uncaught exception:\n{message}", level=applog.LogLevel.ERROR)
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _log_uncaught_exception


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
    _enable_crash_diagnostics()
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
