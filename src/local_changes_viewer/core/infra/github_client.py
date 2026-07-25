import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable

from local_changes_viewer.core.domain.pull_request import PullRequestInfo

_API_BASE = "https://api.github.com"
_TIMEOUT_SECONDS = 10

_REMOTE_URL_RE = re.compile(
    r"^(?:https://github\.com/|ssh://git@github\.com(?:-[\w.-]+)?/|git@github\.com(?:-[\w.-]+)?:)"
    r"(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$"
)


class GitHubError(Exception):
    pass


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
        data = json.loads(body)
        count = len(data) if isinstance(data, list) else 1
        self._on_log(f"GitHub API response: GET {path} -> {count} item(s)")
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
        payload = json.loads(body_bytes)
        if payload.get("errors"):
            self._on_log(f"GitHub API error: POST /graphql -> {payload['errors']}")
            raise GitHubError(f"GitHub GraphQL error: {payload['errors']}")
        return payload["data"]

    def get_authenticated_login(self) -> str:
        data = self._get("/user")
        return data["login"]

    def get_pull_request_review_status(
        self, owner: str, repo: str, number: int
    ) -> tuple[bool | None, int, str | None]:
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              reviewDecision
              reviewThreads(first: 100) {
                nodes { isResolved }
              }
              reviews(last: 1) {
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
        review_decision = pull_request["reviewDecision"]
        approved = None if review_decision is None else review_decision == "APPROVED"
        threads = pull_request["reviewThreads"]["nodes"]
        unresolved_count = sum(1 for thread in threads if not thread["isResolved"])
        review_nodes = pull_request["reviews"]["nodes"]
        last_reviewer = None
        if review_nodes and review_nodes[0]["author"] is not None:
            last_reviewer = review_nodes[0]["author"]["login"]
        return approved, unresolved_count, last_reviewer

    def find_pull_request(self, owner: str, repo: str, branch: str) -> PullRequestInfo | None:
        results = self._get(f"/repos/{owner}/{repo}/pulls?head={owner}:{branch}&state=all")
        if not results:
            return None
        pr = results[0]
        state = "merged" if pr.get("merged_at") else pr["state"]
        review_comments = self._get(
            f"/repos/{owner}/{repo}/pulls/{pr['number']}/comments"
        )
        review_comment_count = len(review_comments) if isinstance(review_comments, list) else 0
        return PullRequestInfo(
            number=pr["number"],
            title=pr["title"],
            state=state,
            url=pr["html_url"],
            comment_count=pr.get("comments", 0),
            review_comment_count=review_comment_count,
            repository=f"{owner}/{repo}",
        )

    def list_authored_open_pull_requests(
        self, author: str, owner_repo_pairs: list[tuple[str, str]]
    ) -> list[PullRequestInfo]:
        self._on_log(
            f"Fetching open PRs authored by '{author}' across {len(owner_repo_pairs)} "
            f"repo(s): {owner_repo_pairs}"
        )
        results = []
        for owner, repo in owner_repo_pairs:
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
                    approved, unresolved_count, last_reviewer = self.get_pull_request_review_status(
                        owner, repo, pr["number"]
                    )
                except GitHubError as exc:
                    self._on_log(
                        f"{owner}/{repo}#{pr['number']}: failed to fetch review status: {exc}"
                    )
                    approved, unresolved_count, last_reviewer = None, 0, None
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
                    )
                )
            self._on_log(f"{owner}/{repo}: {matched} PR(s) matched author '{author}'")
        self._on_log(f"Total matched PRs: {len(results)}")
        return results
