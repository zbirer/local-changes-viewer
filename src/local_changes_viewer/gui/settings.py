from PySide6.QtCore import QByteArray, QSettings

from local_changes_viewer.gui import applog


class AppSettings:
    def __init__(self) -> None:
        self._settings = QSettings("local-changes-viewer", "local-changes-viewer")

    def last_root_folder(self) -> str | None:
        return self._settings.value("last_root_folder", None)

    def set_last_root_folder(self, path: str) -> None:
        self._settings.setValue("last_root_folder", path)

    def window_geometry(self) -> QByteArray | None:
        return self._settings.value("window_geometry", None)

    def set_window_geometry(self, geometry: QByteArray) -> None:
        self._settings.setValue("window_geometry", geometry)

    def splitter_sizes(self) -> list[int] | None:
        value = self._settings.value("splitter_sizes", None)
        if not value:
            return None
        return [int(v) for v in value]

    def set_splitter_sizes(self, sizes: list[int]) -> None:
        self._settings.setValue("splitter_sizes", sizes)

    def diff_view_mode(self) -> str:
        return self._settings.value("diff_view_mode", "unified")

    def set_diff_view_mode(self, mode: str) -> None:
        self._settings.setValue("diff_view_mode", mode)

    def ignore_whitespace(self) -> bool:
        value = self._settings.value("ignore_whitespace", False)
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)

    def set_ignore_whitespace(self, enabled: bool) -> None:
        self._settings.setValue("ignore_whitespace", enabled)

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
