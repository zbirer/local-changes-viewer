from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from local_changes_viewer.core.domain.profile import Profile


class ProfileDialog(QDialog):
    """Manage named profiles, each holding a list of repo names to show when active."""

    profiles_changed = Signal(list)  # list[Profile]

    def __init__(self, profiles: list[Profile], available_repo_names: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Profiles")
        self.resize(500, 400)
        self._profiles = [Profile(name=p.name, repo_names=list(p.repo_names)) for p in profiles]
        self._available_repo_names = sorted(set(available_repo_names))

        self._profile_list = QListWidget()
        self._profile_list.currentRowChanged.connect(self._on_profile_selection_changed)

        new_button = QPushButton("New…")
        new_button.setToolTip("Create a new profile")
        new_button.clicked.connect(self._on_new_profile)

        rename_button = QPushButton("Rename…")
        rename_button.setToolTip("Rename the selected profile")
        rename_button.clicked.connect(self._on_rename_profile)

        delete_button = QPushButton("Delete")
        delete_button.setToolTip("Delete the selected profile")
        delete_button.clicked.connect(self._on_delete_profile)

        profile_buttons_layout = QHBoxLayout()
        profile_buttons_layout.addWidget(new_button)
        profile_buttons_layout.addWidget(rename_button)
        profile_buttons_layout.addWidget(delete_button)

        left_layout = QVBoxLayout()
        left_layout.addWidget(QLabel("Profiles:"))
        left_layout.addWidget(self._profile_list)
        left_layout.addLayout(profile_buttons_layout)

        self._repo_list = QListWidget()
        self._repo_list.itemChanged.connect(self._on_repo_item_changed)

        add_repo_button = QPushButton("Add Repo…")
        add_repo_button.setToolTip("Add a repository by name to the selected profile")
        add_repo_button.clicked.connect(self._on_add_repo)

        right_layout = QVBoxLayout()
        right_layout.addWidget(QLabel("Repositories in selected profile:"))
        right_layout.addWidget(self._repo_list)
        right_layout.addWidget(add_repo_button)

        body_layout = QHBoxLayout()
        body_layout.addLayout(left_layout, 1)
        body_layout.addLayout(right_layout, 1)

        close_button = QPushButton("Close")
        close_button.setToolTip("Close this dialog")
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addLayout(body_layout)
        layout.addWidget(close_button)

        self._refresh_profile_list()

    def _refresh_profile_list(self, *, select_name: str | None = None) -> None:
        self._profile_list.blockSignals(True)
        self._profile_list.clear()
        for profile in self._profiles:
            self._profile_list.addItem(profile.name)
        self._profile_list.blockSignals(False)

        if select_name is not None:
            for row in range(self._profile_list.count()):
                if self._profile_list.item(row).text() == select_name:
                    self._profile_list.setCurrentRow(row)
                    return
        if self._profiles:
            self._profile_list.setCurrentRow(0)
        else:
            self._refresh_repo_list()

    def _current_profile(self) -> Profile | None:
        row = self._profile_list.currentRow()
        if row < 0 or row >= len(self._profiles):
            return None
        return self._profiles[row]

    def _on_profile_selection_changed(self, _row: int) -> None:
        self._refresh_repo_list()

    def _refresh_repo_list(self) -> None:
        profile = self._current_profile()
        self._repo_list.blockSignals(True)
        self._repo_list.clear()
        for repo_name in self._available_repo_names:
            item = QListWidgetItem(repo_name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = profile is not None and repo_name in profile.repo_names
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self._repo_list.addItem(item)
        self._repo_list.blockSignals(False)
        self._repo_list.setEnabled(profile is not None)

    def _on_repo_item_changed(self, item: QListWidgetItem) -> None:
        profile = self._current_profile()
        if profile is None:
            return
        repo_name = item.text()
        checked = item.checkState() == Qt.CheckState.Checked
        if checked and repo_name not in profile.repo_names:
            profile.repo_names.append(repo_name)
        elif not checked and repo_name in profile.repo_names:
            profile.repo_names.remove(repo_name)
        self.profiles_changed.emit(list(self._profiles))

    def _on_add_repo(self) -> None:
        profile = self._current_profile()
        if profile is None:
            return
        name, ok = QInputDialog.getText(self, "Add Repo", "Repository name:")
        name = name.strip()
        if not ok or not name:
            return
        if name not in self._available_repo_names:
            self._available_repo_names = sorted(set(self._available_repo_names) | {name})
        if name not in profile.repo_names:
            profile.repo_names.append(name)
        self._refresh_repo_list()
        self.profiles_changed.emit(list(self._profiles))

    def _on_new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New Profile", "Profile name:")
        name = name.strip()
        if not ok or not name:
            return
        if any(p.name == name for p in self._profiles):
            QMessageBox.warning(self, "Profiles", f"A profile named {name!r} already exists.")
            return
        self._profiles.append(Profile(name=name))
        self._refresh_profile_list(select_name=name)
        self.profiles_changed.emit(list(self._profiles))

    def _on_rename_profile(self) -> None:
        profile = self._current_profile()
        if profile is None:
            return
        name, ok = QInputDialog.getText(self, "Rename Profile", "Profile name:", text=profile.name)
        name = name.strip()
        if not ok or not name or name == profile.name:
            return
        if any(p.name == name for p in self._profiles):
            QMessageBox.warning(self, "Profiles", f"A profile named {name!r} already exists.")
            return
        profile.name = name
        self._refresh_profile_list(select_name=name)
        self.profiles_changed.emit(list(self._profiles))

    def _on_delete_profile(self) -> None:
        profile = self._current_profile()
        if profile is None:
            return
        self._profiles.remove(profile)
        self._refresh_profile_list()
        self.profiles_changed.emit(list(self._profiles))

    def add_repo_to_profile(self, profile_name: str, repo_name: str) -> None:
        for profile in self._profiles:
            if profile.name == profile_name and repo_name not in profile.repo_names:
                profile.repo_names.append(repo_name)
        self._refresh_repo_list()
