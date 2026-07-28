import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from local_changes_viewer.core.domain.pull_request import (
    COMMENT_TYPE_APPROVE_REVIEW,
    COMMENT_TYPE_COMMENT_REVIEW,
    COMMENT_TYPE_ISSUE_COMMENT,
    COMMENT_TYPE_PENDING_REVIEW,
    COMMENT_TYPE_REQUEST_CHANGES_REVIEW,
    COMMENT_TYPE_REVIEW_COMMENT,
    PullRequestDetails,
    PullRequestInfo,
    PullRequestThread,
)

_API_BASE = "https://api.github.com"
_TIMEOUT_SECONDS = 10

_REVIEW_STATE_COMMENT_TYPES = {
    "APPROVED": COMMENT_TYPE_APPROVE_REVIEW,
    "CHANGES_REQUESTED": COMMENT_TYPE_REQUEST_CHANGES_REVIEW,
    "COMMENTED": COMMENT_TYPE_COMMENT_REVIEW,
    "PENDING": COMMENT_TYPE_PENDING_REVIEW,
}


def _title_from_body(body: str, fallback: str) -> str:
    if not body:
        return fallback
    return body.splitlines()[0][:120]

_REMOTE_URL_RE = re.compile(
    r"^(?:https://github\.com/|ssh://git@github\.com(?:-[\w.-]+)?/|git@github\.com(?:-[\w.-]+)?:)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


class GitHubError(Exception):
    pass


_FIND_PR_QUERY = """
query($owner: String!, $repo: String!, $branch: String!) {
  repository(owner: $owner, name: $repo) {
    pullRequests(
      headRefName: $branch
      states: [OPEN, CLOSED, MERGED]
      first: 1
      orderBy: {field: CREATED_AT, direction: DESC}
    ) {
      nodes {
        number
        title
        state
        url
        comments { totalCount }
        reviewComments { totalCount }
      }
    }
  }
}
"""


def parse_github_owner_repo(remote_url: str) -> tuple[str, str] | None:
    match = _REMOTE_URL_RE.match(remote_url.strip())
    if not match:
        return None
    return match.group("owner"), match.group("repo")


class GitHubClient:
    def __init__(self, token: str, on_log: Callable[[str], None] | None = None) -> None:
        self._token = token
        self._on_log = on_log or (lambda _message: None)

    def _get(self, path: str) -> object:
        self._on_log(f"GitHub API request: GET {path}")
        request = urllib.request.Request(
            f"{_API_BASE}{path}",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace") if exc.fp else ""
            self._on_log(
                f"GitHub API error: GET {path} -> HTTP {exc.code} {exc.reason} {body_text}".strip()
            )
            raise GitHubError(f"GitHub API error {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            self._on_log(f"GitHub API request failed: GET {path} -> {exc.reason}")
            raise GitHubError(f"GitHub API request failed: {exc.reason}") from exc
        elapsed_ms = (time.monotonic() - start) * 1000
        data = json.loads(body)
        count = len(data) if isinstance(data, list) else 1
        self._on_log(f"GitHub API response: GET {path} -> {count} item(s) ({elapsed_ms:.0f}ms)")
        return data

    def _graphql(self, query: str, variables: dict) -> dict:
        self._on_log(f"GitHub API request: POST /graphql {variables}")
        body = json.dumps({"query": query, "variables": variables}).encode()
        request = urllib.request.Request(
            f"{_API_BASE}/graphql",
            data=body,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="POST",
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                body_bytes = response.read()
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace") if exc.fp else ""
            self._on_log(f"GitHub API error: POST /graphql -> HTTP {exc.code} {exc.reason} {body_text}".strip())
            raise GitHubError(f"GitHub API error {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            self._on_log(f"GitHub API request failed: POST /graphql -> {exc.reason}")
            raise GitHubError(f"GitHub API request failed: {exc.reason}") from exc
        elapsed_ms = (time.monotonic() - start) * 1000
        payload = json.loads(body_bytes)
        if payload.get("errors"):
            self._on_log(f"GitHub API error: POST /graphql -> {payload['errors']}")
            raise GitHubError(f"GitHub GraphQL error: {payload['errors']}")
        self._on_log(f"GitHub API response: POST /graphql -> ok ({elapsed_ms:.0f}ms)")
        return payload["data"]

    def get_authenticated_login(self) -> str:
        data = self._get("/user")
        return data["login"]

    def get_pull_request_review_status(
        self, owner: str, repo: str, number: int
    ) -> tuple[bool | None, int, str | None, int, str | None]:
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              changedFiles
              reviewDecision
              reviewThreads(first: 100) {
                nodes { isResolved }
              }
              reviews(last: 1) {
                nodes {
                  author { login }
                }
              }
              commits(last: 1) {
                nodes {
                  commit {
                    statusCheckRollup { state }
                  }
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"owner": owner, "repo": repo, "number": number})
        pull_request = data["repository"]["pullRequest"]
        review_decision = pull_request["reviewDecision"]
        approved = None if review_decision is None else review_decision == "APPROVED"
        threads = pull_request["reviewThreads"]["nodes"]
        unresolved_count = sum(1 for thread in threads if not thread["isResolved"])
        review_nodes = pull_request["reviews"]["nodes"]
        last_reviewer = None
        if review_nodes and review_nodes[0]["author"] is not None:
            last_reviewer = review_nodes[0]["author"]["login"]
        changed_files = pull_request["changedFiles"]
        commit_nodes = pull_request["commits"]["nodes"]
        checks_state = None
        if commit_nodes:
            rollup = commit_nodes[0]["commit"]["statusCheckRollup"]
            checks_state = rollup["state"] if rollup else None
        return approved, unresolved_count, last_reviewer, changed_files, checks_state

    def get_pull_request_details(self, owner: str, repo: str, number: int) -> PullRequestDetails:
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              title
              number
              url
              headRefName
              baseRefName
              state
              isDraft
              createdAt
              updatedAt
              comments(last: 1) {
                nodes {
                  author { login }
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"owner": owner, "repo": repo, "number": number})
        pull_request = data["repository"]["pullRequest"]
        status = "Draft" if pull_request["isDraft"] else pull_request["state"].capitalize()
        comment_nodes = pull_request["comments"]["nodes"]
        last_comment_writer = None
        if comment_nodes and comment_nodes[0]["author"] is not None:
            last_comment_writer = comment_nodes[0]["author"]["login"]
        return PullRequestDetails(
            title=pull_request["title"],
            number=pull_request["number"],
            url=pull_request["url"],
            head_ref=pull_request["headRefName"],
            base_ref=pull_request["baseRefName"],
            status=status,
            created_at=pull_request["createdAt"],
            updated_at=pull_request["updatedAt"],
            last_comment_writer=last_comment_writer,
        )

    def get_pull_request_open_threads(
        self, owner: str, repo: str, number: int
    ) -> list[PullRequestThread]:
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              reviewThreads(first: 100) {
                nodes {
                  isResolved
                  comments(first: 1) {
                    nodes {
                      author { login }
                      body
                      createdAt
                      url
                    }
                  }
                }
              }
              comments(first: 100) {
                nodes {
                  author { login }
                  body
                  createdAt
                  url
                }
              }
              reviews(first: 100) {
                nodes {
                  author { login }
                  body
                  createdAt
                  url
                  state
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"owner": owner, "repo": repo, "number": number})
        pull_request = data["repository"]["pullRequest"]
        results: list[PullRequestThread] = []

        for thread in pull_request["reviewThreads"]["nodes"]:
            if thread["isResolved"]:
                continue
            comment_nodes = thread["comments"]["nodes"]
            if not comment_nodes:
                continue
            comment = comment_nodes[0]
            writer = comment["author"]["login"] if comment["author"] is not None else None
            body = comment["body"].strip()
            results.append(
                PullRequestThread(
                    created_at=comment["createdAt"],
                    writer=writer,
                    title=_title_from_body(body, "(no comment text)"),
                    body=body,
                    url=comment["url"],
                    comment_type=COMMENT_TYPE_REVIEW_COMMENT,
                )
            )

        for comment in pull_request["comments"]["nodes"]:
            writer = comment["author"]["login"] if comment["author"] is not None else None
            body = comment["body"].strip()
            results.append(
                PullRequestThread(
                    created_at=comment["createdAt"],
                    writer=writer,
                    title=_title_from_body(body, "(no comment text)"),
                    body=body,
                    url=comment["url"],
                    comment_type=COMMENT_TYPE_ISSUE_COMMENT,
                )
            )

        for review in pull_request["reviews"]["nodes"]:
            comment_type = _REVIEW_STATE_COMMENT_TYPES.get(review["state"])
            if comment_type is None:
                continue
            writer = review["author"]["login"] if review["author"] is not None else None
            body = review["body"].strip()
            results.append(
                PullRequestThread(
                    created_at=review["createdAt"],
                    writer=writer,
                    title=_title_from_body(body, f"{comment_type} (no summary)"),
                    body=body,
                    url=review["url"],
                    comment_type=comment_type,
                )
            )

        results.sort(key=lambda thread: thread.created_at, reverse=True)
        return results

    def find_pull_request(self, owner: str, repo: str, branch: str) -> PullRequestInfo | None:
        data = self._graphql(_FIND_PR_QUERY, {"owner": owner, "repo": repo, "branch": branch})
        nodes = data["repository"]["pullRequests"]["nodes"]
        if not nodes:
            return None
        pr = nodes[0]
        return PullRequestInfo(
            number=pr["number"],
            title=pr["title"],
            state=pr["state"].lower(),
            url=pr["url"],
            comment_count=pr["comments"]["totalCount"],
            review_comment_count=pr["reviewComments"]["totalCount"],
            repository=f"{owner}/{repo}",
        )

    def list_authored_open_pull_requests(
        self,
        author: str,
        owner_repo_pairs: list[tuple[str, str]],
        on_progress: Callable[[str], None] | None = None,
    ) -> list[PullRequestInfo]:
        on_progress = on_progress or (lambda _message: None)
        total = len(owner_repo_pairs)
        self._on_log(
            f"Fetching open PRs authored by '{author}' across {total} "
            f"repo(s): {owner_repo_pairs}"
        )
        results = []
        for index, (owner, repo) in enumerate(owner_repo_pairs, start=1):
            on_progress(f"Fetching your open pull requests… ({index}/{total}: {owner}/{repo})")
            try:
                prs = self._get(f"/repos/{owner}/{repo}/pulls?state=open&per_page=100")
            except GitHubError as exc:
                self._on_log(f"{owner}/{repo}: skipped due to error: {exc}")
                continue
            authors_seen = sorted({(pr.get("user") or {}).get("login", "") for pr in prs})
            self._on_log(
                f"{owner}/{repo}: {len(prs)} open PR(s) found, authors={authors_seen}"
            )
            matched = 0
            for pr in prs:
                pr_author = (pr.get("user") or {}).get("login", "")
                if pr_author.lower() != author.lower():
                    continue
                matched += 1
                try:
                    approved, unresolved_count, last_reviewer, changed_files, checks_state = (
                        self.get_pull_request_review_status(owner, repo, pr["number"])
                    )
                except GitHubError as exc:
                    self._on_log(
                        f"{owner}/{repo}#{pr['number']}: failed to fetch review status: {exc}"
                    )
                    approved, unresolved_count, last_reviewer, changed_files, checks_state = (
                        None,
                        0,
                        None,
                        0,
                        None,
                    )
                results.append(
                    PullRequestInfo(
                        number=pr["number"],
                        title=pr["title"],
                        state="open",
                        url=pr["html_url"],
                        comment_count=pr.get("comments", 0),
                        review_comment_count=0,
                        repository=f"{owner}/{repo}",
                        approved=approved,
                        unresolved_review_thread_count=unresolved_count,
                        last_reviewer=last_reviewer,
                        changed_files=changed_files,
                        checks_state=checks_state,
                    )
                )
            self._on_log(f"{owner}/{repo}: {matched} PR(s) matched author '{author}'")
        self._on_log(f"Total matched PRs: {len(results)}")
        return results
