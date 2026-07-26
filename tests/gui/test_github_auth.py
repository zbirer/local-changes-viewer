import stat

from local_changes_viewer.gui import github_auth


def test_set_and_get_token_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(github_auth, "_TOKEN_FILE_PATH", tmp_path / "github_token.json")

    github_auth.set_token("octocat", "secret-token")

    assert github_auth.get_token("octocat") == "secret-token"


def test_get_token_returns_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(github_auth, "_TOKEN_FILE_PATH", tmp_path / "missing" / "github_token.json")

    assert github_auth.get_token("octocat") is None


def test_delete_token_removes_only_that_user(tmp_path, monkeypatch):
    monkeypatch.setattr(github_auth, "_TOKEN_FILE_PATH", tmp_path / "github_token.json")
    github_auth.set_token("octocat", "secret-token")
    github_auth.set_token("other-user", "other-token")

    github_auth.delete_token("octocat")

    assert github_auth.get_token("octocat") is None
    assert github_auth.get_token("other-user") == "other-token"


def test_delete_token_is_noop_when_user_not_present(tmp_path, monkeypatch):
    monkeypatch.setattr(github_auth, "_TOKEN_FILE_PATH", tmp_path / "github_token.json")

    github_auth.delete_token("nobody")


def test_set_token_writes_file_with_restrictive_permissions(tmp_path, monkeypatch):
    token_path = tmp_path / "github_token.json"
    monkeypatch.setattr(github_auth, "_TOKEN_FILE_PATH", token_path)

    github_auth.set_token("octocat", "secret-token")

    mode = stat.S_IMODE(token_path.stat().st_mode)
    assert mode == 0o600
