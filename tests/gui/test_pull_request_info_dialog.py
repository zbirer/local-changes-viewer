import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from local_changes_viewer.core.domain.pull_request import PullRequestDetails
from local_changes_viewer.gui.pull_request_info_dialog import PullRequestInfoDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _details(**overrides) -> PullRequestDetails:
    defaults = dict(
        title="Add feature",
        number=42,
        url="https://example.com/pr/42",
        head_ref="feature-branch",
        base_ref="main",
        status="Open",
        created_at="2024-01-01T00:00:00Z",
        updated_at="2024-01-02T00:00:00Z",
        last_comment_writer="octocat",
    )
    defaults.update(overrides)
    return PullRequestDetails(**defaults)


def test_title_containing_markup_is_shown_literally(qapp) -> None:
    """A malicious PR title like '<b>x</b>' must render as literal text, not
    bold markup -- GitHub API text is untrusted and QLabel's default
    AutoText format would otherwise interpret it as rich text."""
    dialog = PullRequestInfoDialog(_details(title="<b>x</b>"))

    # Walk the form layout to find the title value label directly -- "Title:"
    # is the first row added in PullRequestInfoDialog.__init__.
    form = dialog.layout().itemAt(0).layout()
    title_value = form.itemAt(0, form.ItemRole.FieldRole).widget()

    assert title_value.text() == "<b>x</b>"
    assert title_value.textFormat() == Qt.TextFormat.PlainText


def test_url_is_html_escaped_in_anchor(qapp) -> None:
    """A URL containing a quote must not be able to break out of the href
    attribute and inject markup into the dialog."""
    malicious_url = 'https://example.com/"><script>x</script>'
    dialog = PullRequestInfoDialog(_details(url=malicious_url))

    # "URL:" is the third row added in PullRequestInfoDialog.__init__.
    form = dialog.layout().itemAt(0).layout()
    url_value = form.itemAt(2, form.ItemRole.FieldRole).widget()

    assert "<script>" not in url_value.text()
    assert "&quot;" in url_value.text()


# ---------------------------------------------------------------------------
# "Copy branch name" (⧉) buttons on the From-branch and To-branch rows must
# each copy their OWN branch name -- a copy/paste bug wiring both buttons to
# the same branch would pass a test that only checks one row.
# ---------------------------------------------------------------------------


def _branch_row_copy_button(dialog: PullRequestInfoDialog, row_index: int) -> QPushButton:
    # "From branch:" is row 3 and "To branch:" is row 4 in
    # PullRequestInfoDialog.__init__ (after Title, Number, URL).
    form = dialog.layout().itemAt(0).layout()
    row_widget = form.itemAt(row_index, form.ItemRole.FieldRole).widget()
    button = row_widget.findChild(QPushButton)
    assert button is not None
    return button


def test_copy_from_branch_button_copies_head_ref(qapp) -> None:
    dialog = PullRequestInfoDialog(_details(head_ref="feature-branch", base_ref="main"))
    QApplication.clipboard().setText("untouched")

    _branch_row_copy_button(dialog, 3).click()

    assert QApplication.clipboard().text() == "feature-branch"


def test_copy_to_branch_button_copies_base_ref(qapp) -> None:
    dialog = PullRequestInfoDialog(_details(head_ref="feature-branch", base_ref="main"))
    QApplication.clipboard().setText("untouched")

    _branch_row_copy_button(dialog, 4).click()

    assert QApplication.clipboard().text() == "main"
