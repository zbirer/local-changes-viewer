import json
import os
from pathlib import Path

_TOKEN_FILE_PATH = Path.home() / ".local-changes-viewer" / "github_token.json"


def _read_tokens() -> dict[str, str]:
    try:
        return json.loads(_TOKEN_FILE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_tokens(tokens: dict[str, str]) -> None:
    _TOKEN_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(_TOKEN_FILE_PATH.parent, 0o700)
    payload = json.dumps(tokens)
    # Write to a sibling temp file, created at mode 0600 from the start (an
    # os.open mode is intersected with, never widened by, the umask — so the
    # plaintext token can never be briefly group/world-readable the way a
    # write_text()-then-chmod() sequence allows), then os.replace() it into
    # place. os.replace is an atomic rename on both POSIX and Windows, so a
    # crash mid-write can never leave a half-written file for _read_tokens to
    # silently mistake for "no tokens".
    tmp_path = _TOKEN_FILE_PATH.with_name(_TOKEN_FILE_PATH.name + ".tmp")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(payload)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    os.replace(tmp_path, _TOKEN_FILE_PATH)


def get_token(username: str) -> str | None:
    return _read_tokens().get(username)


def set_token(username: str, token: str) -> None:
    tokens = _read_tokens()
    tokens[username] = token
    _write_tokens(tokens)


def delete_token(username: str) -> None:
    tokens = _read_tokens()
    if username in tokens:
        del tokens[username]
        _write_tokens(tokens)
