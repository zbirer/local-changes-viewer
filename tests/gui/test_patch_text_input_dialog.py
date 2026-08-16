import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from local_changes_viewer.gui.patch_text_input_dialog import PatchTextInputDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _ok_button(dialog: PatchTextInputDialog):
    return dialog._buttons.button(QDialogButtonBox.StandardButton.Ok)


def test_prefilled_from_clipboard_text(qapp) -> None:
    dialog = PatchTextInputDialog("diff --git a/x b/x\n")

    assert dialog.patch_text() == "diff --git a/x b/x\n"
    assert _ok_button(dialog).isEnabled()


def test_ok_disabled_when_clipboard_text_is_blank(qapp) -> None:
    dialog = PatchTextInputDialog("")

    assert not _ok_button(dialog).isEnabled()


def test_ok_disabled_when_prefilled_text_is_cleared(qapp) -> None:
    dialog = PatchTextInputDialog("some patch text")

    dialog._text_edit.setPlainText("")

    assert not _ok_button(dialog).isEnabled()


def test_patch_text_returns_edited_text(qapp) -> None:
    dialog = PatchTextInputDialog("original text")

    dialog._text_edit.setPlainText("corrected patch text")

    assert dialog.patch_text() == "corrected patch text"


def test_ok_enabled_once_blank_text_is_filled_in(qapp) -> None:
    dialog = PatchTextInputDialog("")
    assert not _ok_button(dialog).isEnabled()

    dialog._text_edit.setPlainText("diff --git a/x b/x\n")

    assert _ok_button(dialog).isEnabled()
