import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QPoint, QUrl
from PySide6.QtWidgets import QApplication

import local_changes_viewer.gui.pull_request_issues_dialog as pull_request_issues_dialog_module
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


# ---------------------------------------------------------------------------
# Hover-to-preview and click-to-open behaviour, driven through the tree's own
# itemEntered/itemClicked signals (not by calling the handlers directly) so
# the wiring set up in PullRequestIssuesDialog.__init__ is exercised too.
# ---------------------------------------------------------------------------

_TITLE_COLUMN = pull_request_issues_dialog_module._TITLE_COLUMN


def test_hovering_a_row_surfaces_popup_with_that_rows_full_comment_body(qapp) -> None:
    threads = [
        _thread(title="first", body="first row's full comment body"),
        _thread(title="second", body="second row's full comment body"),
    ]
    dialog = PullRequestIssuesDialog(threads, pr_number=1)
    second_item = dialog._tree.topLevelItem(1)

    dialog._tree.itemEntered.emit(second_item, _TITLE_COLUMN)

    assert dialog._comment_popup.isVisible()
    assert dialog._comment_popup._label.text() == "second row's full comment body"


def test_hovering_a_non_title_column_hides_the_popup(qapp) -> None:
    thread = _thread(body="a comment")
    dialog = PullRequestIssuesDialog([thread], pr_number=1)
    item = dialog._tree.topLevelItem(0)
    dialog._tree.itemEntered.emit(item, _TITLE_COLUMN)
    assert dialog._comment_popup.isVisible()

    dialog._tree.itemEntered.emit(item, 1)  # "Writer" column, no comment body

    assert not dialog._comment_popup.isVisible()


def test_clicking_the_title_column_opens_that_rows_comment_url(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_on_item_clicked only reacts on _TITLE_COLUMN (index 2, "Title") --
    the visible "Date" column (index 0) already carries its own clickable
    QLabel anchor wired directly to the URL via setOpenExternalLinks, so this
    handler is what lets a click anywhere on the Title text open the same
    per-row URL (read back from the data stashed on column 0)."""
    threads = [
        _thread(title="first", url="https://example.com/pr/1#comment-1"),
        _thread(title="second", url="https://example.com/pr/1#comment-2"),
    ]
    dialog = PullRequestIssuesDialog(threads, pr_number=1)
    second_item = dialog._tree.topLevelItem(1)

    opened_urls: list[QUrl] = []
    monkeypatch.setattr(
        pull_request_issues_dialog_module,
        "QDesktopServices",
        SimpleNamespace(openUrl=lambda url: opened_urls.append(url)),
    )

    dialog._tree.itemClicked.emit(second_item, _TITLE_COLUMN)

    assert len(opened_urls) == 1
    assert opened_urls[0].toString() == "https://example.com/pr/1#comment-2"


def test_clicking_a_non_date_column_does_not_open_a_url(
    qapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread = _thread(url="https://example.com/pr/1#comment")
    dialog = PullRequestIssuesDialog([thread], pr_number=1)
    item = dialog._tree.topLevelItem(0)

    opened_urls: list[QUrl] = []
    monkeypatch.setattr(
        pull_request_issues_dialog_module,
        "QDesktopServices",
        SimpleNamespace(openUrl=lambda url: opened_urls.append(url)),
    )

    dialog._tree.itemClicked.emit(item, 1)  # "Writer" column, not the date/title column

    assert opened_urls == []
