from dataclasses import dataclass


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
    changed_files: int = 0
    checks_state: str | None = None  # e.g. "SUCCESS", "PENDING", "FAILURE"; None if no checks
