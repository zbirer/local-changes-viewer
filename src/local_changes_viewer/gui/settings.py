from PySide6.QtCore import QSettings


class AppSettings:
    def __init__(self) -> None:
        self._settings = QSettings("local-changes-viewer", "local-changes-viewer")

    def last_root_folder(self) -> str | None:
        return self._settings.value("last_root_folder", None)

    def set_last_root_folder(self, path: str) -> None:
        self._settings.setValue("last_root_folder", path)
