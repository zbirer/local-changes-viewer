from PySide6.QtCore import QSettings

from local_changes_viewer.gui import applog


class AppSettings:
    def __init__(self) -> None:
        self._settings = QSettings("local-changes-viewer", "local-changes-viewer")

    def last_root_folder(self) -> str | None:
        return self._settings.value("last_root_folder", None)

    def set_last_root_folder(self, path: str) -> None:
        self._settings.setValue("last_root_folder", path)

    def collapsed_node_keys(self) -> set[str]:
        value = self._settings.value("collapsed_node_keys", [])
        applog.log(f"collapsed_node_keys() raw QSettings value: {value!r} (type={type(value)})")
        if not value:
            return set()
        # QSettings on some platforms collapses a single-item list back into a bare string.
        if isinstance(value, str):
            value = [value]
        result = set(value)
        applog.log(f"collapsed_node_keys() -> {result!r}")
        return result

    def set_collapsed_node_keys(self, keys: set[str]) -> None:
        applog.log(f"set_collapsed_node_keys({sorted(keys)!r})")
        self._settings.setValue("collapsed_node_keys", sorted(keys))
        self._settings.sync()
