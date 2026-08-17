import datetime
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
    #
    # A timestamped banner is written to the log unconditionally (even on a
    # TTY, where the dump itself goes to stderr instead) so successive runs
    # in the same 63 KB file are tellable apart -- without this, a crash
    # dumped to the file on one run and a crash dumped to the terminal on
    # the next left no marker of when the terminal-only run even happened.
    log_file = CRASH_LOG_PATH.open("a")
    banner = datetime.datetime.now().isoformat(timespec="seconds")
    log_file.write(f"\n----- {banner} -----\n")
    log_file.flush()

    # faulthandler needs a real file descriptor (fileno()) to write to from
    # a signal handler, which rules out a Python-level tee wrapper -- so the
    # destination is one or the other, decided once at startup: stderr when
    # there is a terminal actually reading it (a `python -m ...` launch),
    # otherwise the log file (e.g. a .app bundle launched with no console,
    # where a stderr dump would go straight to /dev/null and never be seen).
    if sys.stderr is not None and sys.stderr.isatty():
        # `log_file` was only needed for the banner above -- faulthandler
        # itself targets the terminal here, so there is nothing left to
        # keep this handle open for.
        log_file.close()
        faulthandler.enable(file=sys.stderr, all_threads=True)
        print(f"Crash log: {CRASH_LOG_PATH}", file=sys.stderr)
    else:
        faulthandler.enable(file=log_file, all_threads=True)

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
