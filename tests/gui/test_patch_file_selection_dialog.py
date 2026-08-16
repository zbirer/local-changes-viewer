import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from local_changes_viewer.core.domain.file_change import ChangeType, FileChange
from local_changes_viewer.gui.patch_file_selection_dialog import PatchFileSelectionDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _changes() -> list[FileChange]:
    return [
        FileChange(path=Path("a.txt"), change_type=ChangeType.MODIFIED),
        FileChange(path=Path("b/new.txt"), change_type=ChangeType.UNTRACKED),
        FileChange(path=Path("c.txt"), change_type=ChangeType.DELETED),
    ]


def _ok_button(dialog: PatchFileSelectionDialog):
    return dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)


def test_all_rows_checked_on_open(qapp) -> None:
    dialog = PatchFileSelectionDialog(_changes())

    assert dialog.selected_paths() == [Path("a.txt"), Path("b/new.txt"), Path("c.txt")]
    assert _ok_button(dialog).isEnabled()


def test_unchecking_a_row_excludes_it_from_the_selection(qapp) -> None:
    dialog = PatchFileSelectionDialog(_changes())

    dialog._list.item(1).setCheckState(Qt.CheckState.Unchecked)

    assert dialog.selected_paths() == [Path("a.txt"), Path("c.txt")]


def test_deselect_all_button_unchecks_every_row_and_disables_ok(qapp) -> None:
    dialog = PatchFileSelectionDialog(_changes())

    dialog._deselect_all_button.click()

    assert dialog.selected_paths() == []
    assert not _ok_button(dialog).isEnabled()


def test_select_all_button_rechecks_every_row_and_reenables_ok(qapp) -> None:
    dialog = PatchFileSelectionDialog(_changes())
    dialog._deselect_all_button.click()

    dialog._select_all_button.click()

    assert dialog.selected_paths() == [Path("a.txt"), Path("b/new.txt"), Path("c.txt")]
    assert _ok_button(dialog).isEnabled()


def test_ok_disabled_the_moment_the_last_checked_row_is_unchecked(qapp) -> None:
    dialog = PatchFileSelectionDialog([FileChange(path=Path("only.txt"), change_type=ChangeType.MODIFIED)])
    assert _ok_button(dialog).isEnabled()

    dialog._list.item(0).setCheckState(Qt.CheckState.Unchecked)

    assert not _ok_button(dialog).isEnabled()


def test_cancel_button_rejects_the_dialog(qapp) -> None:
    dialog = PatchFileSelectionDialog(_changes())
    rejected: list = []
    dialog.rejected.connect(lambda: rejected.append(True))

    dialog._buttons.button(QDialogButtonBox.StandardButton.Cancel).click()

    assert rejected == [True]


def test_single_file_target_still_shows_one_checked_row(qapp) -> None:
    """The brief is explicit that a single-file target goes through this same
    dialog rather than being special-cased away -- so a one-row list must
    behave identically to a multi-row one (checked by default, OK enabled)."""
    dialog = PatchFileSelectionDialog(
        [FileChange(path=Path("solo.txt"), change_type=ChangeType.MODIFIED)]
    )

    assert dialog.selected_paths() == [Path("solo.txt")]
    assert _ok_button(dialog).isEnabled()
