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
    _TOKEN_FILE_PATH.write_text(json.dumps(tokens))
    os.chmod(_TOKEN_FILE_PATH, 0o600)


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
