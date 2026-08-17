import json
from io import BytesIO
from unittest.mock import patch

import pytest

from local_changes_viewer.core.infra.github_client import (
    GitHubClient,
    GitHubError,
    parse_github_owner_repo,
)


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/owner/repo.git", ("owner", "repo")),
        ("https://github.com/owner/repo", ("owner", "repo")),
        ("git@github.com:owner/repo.git", ("owner", "repo")),
        ("git@github.com:owner/repo", ("owner", "repo")),
        ("ssh://git@github.com/owner/repo.git", ("owner", "repo")),
        ("ssh://git@github.com/owner/repo", ("owner", "repo")),
        ("git@github.com-personal:owner/repo.git", ("owner", "repo")),
        ("ssh://git@github.com-personal/owner/repo.git", ("owner", "repo")),
        ("https://gitlab.com/owner/repo.git", None),
        ("not a url", None),
    ],
)
def test_parse_github_owner_repo(url, expected):
    assert parse_github_owner_repo(url) == expected


class _FakeResponse:
    def __init__(self, payload, headers: dict | None = None):
        self._body = json.dumps(payload).encode()
        # A plain dict supports .get() the same way http.client.HTTPMessage
        # does, which is all _get_page needs to read the Link header.
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_get_authenticated_login():
    with patch("urllib.request.urlopen", return_value=_FakeResponse({"login": "octocat"})):
        login = GitHubClient("token").get_authenticated_login()

    assert login == "octocat"


def test_find_pull_request_returns_none_when_no_matches():
    payload = {"data": {"repository": {"pullRequests": {"nodes": []}}}}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        result = GitHubClient("token").find_pull_request("owner", "repo", "feature")

    assert result is None


def test_find_pull_request_returns_info_when_found():
    payload = {
        "data": {
            "repository": {
                "pullRequests": {
                    "nodes": [
                        {
                            "number": 42,
                            "title": "Add feature",
                            "state": "OPEN",
                            "url": "https://github.com/owner/repo/pull/42",
                            "comments": {"totalCount": 3},
                        }
                    ]
                }
            }
        }
    }

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        result = GitHubClient("token").find_pull_request("owner", "repo", "feature")

    assert result is not None
    assert result.number == 42
    assert result.title == "Add feature"
    assert result.state == "open"
    assert result.url == "https://github.com/owner/repo/pull/42"
    assert result.comment_count == 3
    assert result.review_comment_count == 0
    assert result.repository == "owner/repo"


def test_find_pull_request_lowercases_merged_state():
    payload = {
        "data": {
            "repository": {
                "pullRequests": {
                    "nodes": [
                        {
                            "number": 42,
                            "title": "Add feature",
                            "state": "MERGED",
                            "url": "https://github.com/owner/repo/pull/42",
                            "comments": {"totalCount": 0},
                        }
                    ]
                }
            }
        }
    }

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        result = GitHubClient("token").find_pull_request("owner", "repo", "feature")

    assert result.state == "merged"


def test_find_pull_request_raises_github_error_on_graphql_errors():
    payload = {"errors": [{"message": "Something went wrong"}]}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        with pytest.raises(GitHubError):
            GitHubClient("token").find_pull_request("owner", "repo", "feature")


def test_find_pull_request_hardcodes_review_comment_count_to_zero():
    payload = {
        "data": {
            "repository": {
                "pullRequests": {
                    "nodes": [
                        {
                            "number": 42,
                            "title": "Add feature",
                            "state": "OPEN",
                            "url": "https://github.com/owner/repo/pull/42",
                            "comments": {"totalCount": 5},
                        }
                    ]
                }
            }
        }
    }

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        result = GitHubClient("token").find_pull_request("owner", "repo", "feature")

    assert result.comment_count == 5
    assert result.review_comment_count == 0


def test_get_authenticated_login_raises_github_error_on_http_error():
    import urllib.error

    http_error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, BytesIO())
    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(GitHubError):
            GitHubClient("bad-token").get_authenticated_login()


def test_list_authored_open_pull_requests_empty_pairs_returns_empty_list():
    result = GitHubClient("token").list_authored_open_pull_requests("octocat", [])

    assert result == []


def test_list_authored_open_pull_requests_returns_matches_filtered_by_author():
    pulls_payload = [
        {
            "number": 7,
            "title": "Fix bug",
            "html_url": "https://github.com/owner/repo/pull/7",
            "comments": 2,
            "user": {"login": "octocat"},
        },
        {
            "number": 8,
            "title": "Someone else's PR",
            "html_url": "https://github.com/owner/repo/pull/8",
            "comments": 0,
            "user": {"login": "someone-else"},
        },
    ]
    review_status_payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "changedFiles": 3,
                    "reviewDecision": "APPROVED",
                    "reviewThreads": {"nodes": [{"isResolved": True}, {"isResolved": False}]},
                    "reviews": {"nodes": [{"author": {"login": "reviewer-one"}, "submittedAt": "2026-01-01T00:00:00Z"}]},
                    "commits": {
                        "nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]
                    },
                }
            }
        }
    }

    responses = [_FakeResponse(pulls_payload), _FakeResponse(review_status_payload)]

    with patch("urllib.request.urlopen", side_effect=responses):
        results = GitHubClient("token").list_authored_open_pull_requests(
            "octocat", [("owner", "repo")]
        )

    assert len(results) == 1
    assert results[0].number == 7
    assert results[0].repository == "owner/repo"
    assert results[0].state == "open"
    assert results[0].approved is True
    assert results[0].unresolved_review_thread_count == 1
    assert results[0].last_reviewer == "reviewer-one"
    assert results[0].last_reviewed_at == "2026-01-01T00:00:00Z"
    assert results[0].changed_files == 3
    assert results[0].checks_state == "SUCCESS"


def test_get_pull_request_review_status_returns_approval_unresolved_count_and_last_reviewer():
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "changedFiles": 5,
                    "reviewDecision": "CHANGES_REQUESTED",
                    "reviewThreads": {
                        "nodes": [
                            {"isResolved": False},
                            {"isResolved": False},
                            {"isResolved": True},
                        ]
                    },
                    "reviews": {"nodes": [{"author": {"login": "reviewer-two"}, "submittedAt": "2026-01-02T00:00:00Z"}]},
                    "commits": {
                        "nodes": [{"commit": {"statusCheckRollup": {"state": "PENDING"}}}]
                    },
                }
            }
        }
    }

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        (
            approved,
            unresolved_count,
            last_reviewer,
            last_reviewed_at,
            changed_files,
            checks_state,
        ) = GitHubClient("token").get_pull_request_review_status("owner", "repo", 7)

    assert approved is False
    assert unresolved_count == 2
    assert last_reviewer == "reviewer-two"
    assert last_reviewed_at == "2026-01-02T00:00:00Z"
    assert changed_files == 5
    assert checks_state == "PENDING"


def test_get_pull_request_review_status_returns_none_reviewer_when_no_reviews():
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "changedFiles": 0,
                    "reviewDecision": None,
                    "reviewThreads": {"nodes": []},
                    "reviews": {"nodes": []},
                    "commits": {"nodes": []},
                }
            }
        }
    }

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        (
            approved,
            unresolved_count,
            last_reviewer,
            last_reviewed_at,
            changed_files,
            checks_state,
        ) = GitHubClient("token").get_pull_request_review_status("owner", "repo", 7)

    assert approved is None
    assert unresolved_count == 0
    assert last_reviewer is None
    assert last_reviewed_at is None
    assert changed_files == 0
    assert checks_state is None


def test_get_pull_request_review_status_paginates_review_threads_beyond_first_page():
    """A PR with more than 100 review threads must not have unresolved_count
    computed from only the first (oldest) page."""
    first_page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "changedFiles": 5,
                    "reviewDecision": "CHANGES_REQUESTED",
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                        "nodes": [{"isResolved": False}, {"isResolved": True}],
                    },
                    "reviews": {
                        "nodes": [
                            {"author": {"login": "reviewer-two"}, "submittedAt": "2026-01-02T00:00:00Z"}
                        ]
                    },
                    "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "PENDING"}}}]},
                }
            }
        }
    }
    second_page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [{"isResolved": False}, {"isResolved": False}],
                    }
                }
            }
        }
    }

    with patch(
        "urllib.request.urlopen",
        side_effect=[_FakeResponse(first_page), _FakeResponse(second_page)],
    ):
        (
            approved,
            unresolved_count,
            _last_reviewer,
            _last_reviewed_at,
            _changed_files,
            _checks_state,
        ) = GitHubClient("token").get_pull_request_review_status("owner", "repo", 7)

    # 1 unresolved on page 1 + 2 unresolved on page 2 = 3. Without following
    # the cursor this would stop at page 1's count of 1.
    assert unresolved_count == 3
    assert approved is False


def test_get_pull_request_details_returns_open_status_and_last_comment_writer():
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "title": "Add feature",
                    "number": 42,
                    "url": "https://github.com/owner/repo/pull/42",
                    "headRefName": "feature-branch",
                    "baseRefName": "main",
                    "state": "OPEN",
                    "isDraft": False,
                    "createdAt": "2026-07-01T10:00:00Z",
                    "updatedAt": "2026-07-02T12:00:00Z",
                    "comments": {"nodes": [{"author": {"login": "commenter-one"}}]},
                }
            }
        }
    }

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        details = GitHubClient("token").get_pull_request_details("owner", "repo", 42)

    assert details.title == "Add feature"
    assert details.number == 42
    assert details.url == "https://github.com/owner/repo/pull/42"
    assert details.head_ref == "feature-branch"
    assert details.base_ref == "main"
    assert details.status == "Open"
    assert details.created_at == "2026-07-01T10:00:00Z"
    assert details.updated_at == "2026-07-02T12:00:00Z"
    assert details.last_comment_writer == "commenter-one"


def test_get_pull_request_details_reports_draft_status_and_no_comments():
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "title": "WIP feature",
                    "number": 43,
                    "url": "https://github.com/owner/repo/pull/43",
                    "headRefName": "wip-branch",
                    "baseRefName": "main",
                    "state": "OPEN",
                    "isDraft": True,
                    "createdAt": "2026-07-01T10:00:00Z",
                    "updatedAt": "2026-07-01T10:00:00Z",
                    "comments": {"nodes": []},
                }
            }
        }
    }

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        details = GitHubClient("token").get_pull_request_details("owner", "repo", 43)

    assert details.status == "Draft"
    assert details.last_comment_writer is None


def _empty_threads_payload(**overrides):
    payload = {
        "reviewThreads": {"nodes": []},
        "comments": {"nodes": []},
        "reviews": {"nodes": []},
    }
    payload.update(overrides)
    return {"data": {"repository": {"pullRequest": payload}}}


def test_get_pull_request_open_threads_returns_only_unresolved_review_comments():
    payload = _empty_threads_payload(
        reviewThreads={
            "nodes": [
                {
                    "isResolved": True,
                    "comments": {
                        "nodes": [
                            {
                                "author": {"login": "reviewer-one"},
                                "body": "Resolved comment",
                                "createdAt": "2026-07-01T10:00:00Z",
                                "url": "https://github.com/owner/repo/pull/42#discussion_r1",
                            }
                        ]
                    },
                },
                {
                    "isResolved": False,
                    "comments": {
                        "nodes": [
                            {
                                "author": {"login": "reviewer-two"},
                                "body": "Please fix this\nmore detail",
                                "createdAt": "2026-07-02T11:00:00Z",
                                "url": "https://github.com/owner/repo/pull/42#discussion_r2",
                            }
                        ]
                    },
                },
            ]
        }
    )

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        threads = GitHubClient("token").get_pull_request_open_threads("owner", "repo", 42)

    assert len(threads) == 1
    assert threads[0].writer == "reviewer-two"
    assert threads[0].title == "Please fix this"
    assert threads[0].body == "Please fix this\nmore detail"
    assert threads[0].created_at == "2026-07-02T11:00:00Z"
    assert threads[0].url == "https://github.com/owner/repo/pull/42#discussion_r2"
    assert threads[0].comment_type == "Review Comments (Inline Comments)"


def test_get_pull_request_open_threads_returns_issue_comments():
    payload = _empty_threads_payload(
        comments={
            "nodes": [
                {
                    "author": {"login": "commenter-one"},
                    "body": "General feedback on the PR",
                    "createdAt": "2026-07-03T09:00:00Z",
                    "url": "https://github.com/owner/repo/pull/42#issuecomment-1",
                }
            ]
        }
    )

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        threads = GitHubClient("token").get_pull_request_open_threads("owner", "repo", 42)

    assert len(threads) == 1
    assert threads[0].writer == "commenter-one"
    assert threads[0].title == "General feedback on the PR"
    assert threads[0].comment_type == "Issue Comments (General Comments)"


def test_get_pull_request_open_threads_maps_review_states_to_comment_types():
    payload = _empty_threads_payload(
        reviews={
            "nodes": [
                {
                    "author": {"login": "reviewer-a"},
                    "body": "Looks good",
                    "createdAt": "2026-07-04T09:00:00Z",
                    "url": "https://github.com/owner/repo/pull/42#pullrequestreview-1",
                    "state": "APPROVED",
                },
                {
                    "author": {"login": "reviewer-b"},
                    "body": "Please address these issues",
                    "createdAt": "2026-07-04T10:00:00Z",
                    "url": "https://github.com/owner/repo/pull/42#pullrequestreview-2",
                    "state": "CHANGES_REQUESTED",
                },
                {
                    "author": {"login": "reviewer-c"},
                    "body": "Some general notes",
                    "createdAt": "2026-07-04T11:00:00Z",
                    "url": "https://github.com/owner/repo/pull/42#pullrequestreview-3",
                    "state": "COMMENTED",
                },
                {
                    "author": {"login": "reviewer-d"},
                    "body": "",
                    "createdAt": "2026-07-04T12:00:00Z",
                    "url": "https://github.com/owner/repo/pull/42#pullrequestreview-4",
                    "state": "PENDING",
                },
                {
                    "author": {"login": "reviewer-e"},
                    "body": "Dismissed review",
                    "createdAt": "2026-07-04T13:00:00Z",
                    "url": "https://github.com/owner/repo/pull/42#pullrequestreview-5",
                    "state": "DISMISSED",
                },
            ]
        }
    )

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        threads = GitHubClient("token").get_pull_request_open_threads("owner", "repo", 42)

    # Results are sorted newest-first; reviews were created at 09:00, 10:00, 11:00, 12:00.
    comment_types = [thread.comment_type for thread in threads]
    assert comment_types == [
        "Pending / Draft Review",
        "Comment Review",
        "Request Changes Review",
        "Approve Review",
    ]
    pending = threads[0]
    assert pending.title == "Pending / Draft Review (no summary)"


def test_get_pull_request_open_threads_sorts_across_categories_newest_first():
    payload = _empty_threads_payload(
        reviewThreads={
            "nodes": [
                {
                    "isResolved": False,
                    "comments": {
                        "nodes": [
                            {
                                "author": {"login": "reviewer-one"},
                                "body": "Oldest inline comment",
                                "createdAt": "2026-07-01T08:00:00Z",
                                "url": "https://github.com/owner/repo/pull/42#discussion_r1",
                            }
                        ]
                    },
                }
            ]
        },
        comments={
            "nodes": [
                {
                    "author": {"login": "commenter-one"},
                    "body": "Newest issue comment",
                    "createdAt": "2026-07-03T08:00:00Z",
                    "url": "https://github.com/owner/repo/pull/42#issuecomment-1",
                }
            ]
        },
        reviews={
            "nodes": [
                {
                    "author": {"login": "reviewer-two"},
                    "body": "Middle review",
                    "createdAt": "2026-07-02T08:00:00Z",
                    "url": "https://github.com/owner/repo/pull/42#pullrequestreview-1",
                    "state": "COMMENTED",
                }
            ]
        },
    )

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        threads = GitHubClient("token").get_pull_request_open_threads("owner", "repo", 42)

    assert [thread.title for thread in threads] == [
        "Newest issue comment",
        "Middle review",
        "Oldest inline comment",
    ]


def test_get_pull_request_open_threads_returns_empty_list_when_nothing_open():
    payload = _empty_threads_payload()

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        threads = GitHubClient("token").get_pull_request_open_threads("owner", "repo", 42)

    assert threads == []


def test_get_pull_request_open_threads_paginates_review_threads_beyond_first_page():
    """A PR with more than 100 review threads must not silently drop the
    threads past the first (oldest) page."""
    first_page = _empty_threads_payload(
        reviewThreads={
            "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
            "nodes": [
                {
                    "isResolved": False,
                    "comments": {
                        "nodes": [
                            {
                                "author": {"login": "reviewer-one"},
                                "body": "First page comment",
                                "createdAt": "2026-07-01T10:00:00Z",
                                "url": "https://github.com/owner/repo/pull/42#discussion_r1",
                            }
                        ]
                    },
                }
            ],
        }
    )
    second_page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "isResolved": False,
                                "comments": {
                                    "nodes": [
                                        {
                                            "author": {"login": "reviewer-two"},
                                            "body": "Second page comment",
                                            "createdAt": "2026-07-02T10:00:00Z",
                                            "url": "https://github.com/owner/repo/pull/42#discussion_r2",
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                }
            }
        }
    }

    with patch(
        "urllib.request.urlopen",
        side_effect=[_FakeResponse(first_page), _FakeResponse(second_page)],
    ):
        threads = GitHubClient("token").get_pull_request_open_threads("owner", "repo", 42)

    bodies = {thread.body for thread in threads}
    assert bodies == {"First page comment", "Second page comment"}


def test_list_authored_open_pull_requests_skips_repo_on_error():
    import urllib.error

    http_error = urllib.error.HTTPError("url", 404, "Not Found", {}, BytesIO())
    with patch("urllib.request.urlopen", side_effect=http_error):
        results = GitHubClient("token").list_authored_open_pull_requests(
            "octocat", [("owner", "missing-repo")]
        )

    assert results == []


def test_list_authored_open_pull_requests_follows_link_header_pagination():
    """A repo with more than 100 open PRs must not silently drop the PRs past
    the first REST page."""
    page_one = [
        {
            "number": 1,
            "title": "PR one",
            "html_url": "https://github.com/owner/repo/pull/1",
            "comments": 0,
            "user": {"login": "octocat"},
        }
    ]
    page_two = [
        {
            "number": 2,
            "title": "PR two",
            "html_url": "https://github.com/owner/repo/pull/2",
            "comments": 0,
            "user": {"login": "octocat"},
        }
    ]
    review_status_payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "changedFiles": 0,
                    "reviewDecision": None,
                    "reviewThreads": {"nodes": []},
                    "reviews": {"nodes": []},
                    "commits": {"nodes": []},
                }
            }
        }
    }
    next_link = '<https://api.github.com/repositories/1/pulls?per_page=100&page=2>; rel="next"'
    responses = [
        _FakeResponse(page_one, headers={"Link": next_link}),
        _FakeResponse(page_two),
        _FakeResponse(review_status_payload),
        _FakeResponse(review_status_payload),
    ]

    with patch("urllib.request.urlopen", side_effect=responses):
        results = GitHubClient("token").list_authored_open_pull_requests(
            "octocat", [("owner", "repo")]
        )

    assert [result.number for result in results] == [1, 2]


def test_list_authored_open_pull_requests_bounds_link_header_pagination(monkeypatch):
    """A malformed or looping Link header (always pointing at "next") must not
    hang the app forever."""
    from local_changes_viewer.core.infra import github_client as github_client_module

    monkeypatch.setattr(github_client_module, "_MAX_PAGINATION_PAGES", 3)
    infinite_link = '<https://api.github.com/repositories/1/pulls?per_page=100&page=2>; rel="next"'
    never_ending_page = [
        {
            "number": 1,
            "title": "Never-ending PR",
            "html_url": "https://github.com/owner/repo/pull/1",
            "comments": 0,
            "user": {"login": "someone-else"},
        }
    ]
    call_count = {"n": 0}

    def fake_urlopen(_request, timeout=None):
        call_count["n"] += 1
        return _FakeResponse(never_ending_page, headers={"Link": infinite_link})

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        results = GitHubClient("token").list_authored_open_pull_requests(
            "octocat", [("owner", "repo")]
        )

    # Author never matches "octocat", so this isolates the REST pagination
    # bound: exactly _MAX_PAGINATION_PAGES page fetches, never more.
    assert call_count["n"] == 3
    assert results == []
