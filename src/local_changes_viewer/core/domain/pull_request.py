from dataclasses import dataclass

COMMENT_TYPE_ISSUE_COMMENT = "Issue Comments (General Comments)"
COMMENT_TYPE_REVIEW_COMMENT = "Review Comments (Inline Comments)"
COMMENT_TYPE_COMMENT_REVIEW = "Comment Review"
COMMENT_TYPE_APPROVE_REVIEW = "Approve Review"
COMMENT_TYPE_REQUEST_CHANGES_REVIEW = "Request Changes Review"
COMMENT_TYPE_PENDING_REVIEW = "Pending / Draft Review"


@dataclass(frozen=True)
class PullRequestInfo:
    number: int
    title: str
    state: str  # "open", "closed", or "merged"
    url: str
    comment_count: int
    review_comment_count: int
    repository: str = ""  # "owner/repo"
    approved: bool | None = None  # None means not fetched
    unresolved_review_thread_count: int = 0
    last_reviewer: str | None = None
    last_reviewed_at: str | None = None
    changed_files: int = 0
    checks_state: str | None = None  # e.g. "SUCCESS", "PENDING", "FAILURE"; None if no checks


@dataclass(frozen=True)
class PullRequestDetails:
    title: str
    number: int
    url: str
    head_ref: str
    base_ref: str
    status: str
    created_at: str
    updated_at: str
    last_comment_writer: str | None


@dataclass(frozen=True)
class PullRequestThread:
    created_at: str
    writer: str | None
    title: str
    body: str
    url: str
    comment_type: str
