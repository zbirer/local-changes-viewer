import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication

from local_changes_viewer.core.domain.pull_request import (
    COMMENT_TYPE_ISSUE_COMMENT,
    PullRequestThread,
)
from local_changes_viewer.gui.pull_request_issues_dialog import PullRequestIssuesDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _thread(**overrides) -> PullRequestThread:
    defaults = dict(
        created_at="2024-01-01T00:00:00Z",
        writer="octocat",
        title="Please fix this",
        body="a comment",
        url="https://example.com/pr/1#comment",
        comment_type=COMMENT_TYPE_ISSUE_COMMENT,
    )
    defaults.update(overrides)
    return PullRequestThread(**defaults)


def test_comment_body_containing_markup_is_shown_literally(qapp) -> None:
    """A malicious comment body like '<b>x</b>' must render literally in the
    hover popup, not as bold markup -- comment bodies are untrusted API text."""
    thread = _thread(body="<b>x</b>")
    dialog = PullRequestIssuesDialog([thread], pr_number=1)

    dialog._comment_popup.show_near(thread.body, QPoint(0, 0))

    assert dialog._comment_popup._label.text() == "<b>x</b>"
    assert dialog._comment_popup._label.textFormat() == Qt.TextFormat.PlainText


def test_thread_url_is_html_escaped_in_date_anchor(qapp) -> None:
    """A URL containing a quote must not be able to break out of the href
    attribute and inject markup into the date column's anchor."""
    malicious_url = 'https://example.com/"><script>x</script>'
    thread = _thread(url=malicious_url)
    dialog = PullRequestIssuesDialog([thread], pr_number=1)

    date_label = dialog._tree.itemWidget(dialog._tree.topLevelItem(0), 0)

    assert "<script>" not in date_label.text()
    assert "&quot;" in date_label.text()
