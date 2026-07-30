import json

from PySide6.QtCore import QByteArray, QSettings

from local_changes_viewer.core.domain.folder_filter_rule import FolderFilterMode, FolderFilterRule
from local_changes_viewer.core.domain.profile import Profile
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

    def ignore_md_files(self) -> bool:
        value = self._settings.value("ignore_md_files", False)
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)

    def set_ignore_md_files(self, enabled: bool) -> None:
        self._settings.setValue("ignore_md_files", enabled)

    def hide_repos_without_changes(self) -> bool:
        value = self._settings.value("hide_repos_without_changes", False)
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)

    def set_hide_repos_without_changes(self, enabled: bool) -> None:
        self._settings.setValue("hide_repos_without_changes", enabled)

    def folder_filter_rules(self) -> list[FolderFilterRule]:
        raw = self._settings.value("folder_filter_rules", [])
        if not raw:
            return []
        if isinstance(raw, str):
            # QSettings can collapse a single-element string list back to a
            # bare string on some platforms/backends.
            raw = [raw]
        rules = []
        for entry in raw:
            mode_value, _, text = entry.partition(":")
            try:
                rules.append(FolderFilterRule(text=text, mode=FolderFilterMode(mode_value)))
            except ValueError:
                applog.log(
                    f"Ignoring malformed folder filter rule entry: {entry!r}",
                    level=applog.LogLevel.WARNING,
                )
        applog.log(
            f"Loaded folder filter rules from settings: "
            f"[{', '.join(f'{r.mode.value}:{r.text!r}' for r in rules)}]",
            level=applog.LogLevel.DEBUG,
        )
        return rules

    def set_folder_filter_rules(self, rules: list[FolderFilterRule]) -> None:
        self._settings.setValue(
            "folder_filter_rules", [f"{rule.mode.value}:{rule.text}" for rule in rules]
        )

    def auto_refresh_minutes(self) -> int:
        value = self._settings.value("auto_refresh_minutes", 0)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def set_auto_refresh_minutes(self, minutes: int) -> None:
        self._settings.setValue("auto_refresh_minutes", minutes)

    def log_level(self) -> str:
        return str(self._settings.value("log_level", "INFO"))

    def set_log_level(self, level_name: str) -> None:
        self._settings.setValue("log_level", level_name)

    def github_username(self) -> str | None:
        return self._settings.value("github_username", None)

    def set_github_username(self, username: str) -> None:
        self._settings.setValue("github_username", username)

    def clear_github_username(self) -> None:
        self._settings.remove("github_username")

    def sync_side_by_side_scroll(self) -> bool:
        value = self._settings.value("sync_side_by_side_scroll", True)
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)

    def set_sync_side_by_side_scroll(self, enabled: bool) -> None:
        self._settings.setValue("sync_side_by_side_scroll", enabled)

    def always_reload_diff(self) -> bool:
        value = self._settings.value("always_reload_diff", True)
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)

    def set_always_reload_diff(self, enabled: bool) -> None:
        self._settings.setValue("always_reload_diff", enabled)

    def profiles(self) -> list[Profile]:
        raw = self._settings.value("profiles", "")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            applog.log(f"Ignoring malformed profiles value: {raw!r}", level=applog.LogLevel.WARNING)
            return []
        return [
            Profile(name=entry["name"], repo_names=list(entry.get("repo_names", [])))
            for entry in data
        ]

    def set_profiles(self, profiles: list[Profile]) -> None:
        data = [{"name": p.name, "repo_names": list(p.repo_names)} for p in profiles]
        self._settings.setValue("profiles", json.dumps(data))

    def active_profile_name(self) -> str | None:
        return self._settings.value("active_profile_name", None) or None

    def set_active_profile_name(self, name: str | None) -> None:
        if name is None:
            self._settings.remove("active_profile_name")
        else:
            self._settings.setValue("active_profile_name", name)

    def collapsed_node_keys(self) -> set[str]:
        value = self._settings.value("collapsed_node_keys", [])
        applog.log(
            f"collapsed_node_keys() raw QSettings value: {value!r} (type={type(value)})",
            level=applog.LogLevel.DEBUG,
        )
        if not value:
            return set()
        # QSettings on some platforms collapses a single-item list back into a bare string.
        if isinstance(value, str):
            value = [value]
        result = set(value)
        applog.log(f"collapsed_node_keys() -> {result!r}", level=applog.LogLevel.DEBUG)
        return result

    def set_collapsed_node_keys(self, keys: set[str]) -> None:
        applog.log(f"set_collapsed_node_keys({sorted(keys)!r})", level=applog.LogLevel.DEBUG)
        self._settings.setValue("collapsed_node_keys", sorted(keys))
        self._settings.sync()
