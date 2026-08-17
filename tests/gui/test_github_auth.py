import stat

import pytest

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


def test_set_token_restricts_parent_directory_permissions(tmp_path, monkeypatch):
    token_dir = tmp_path / "config"
    monkeypatch.setattr(github_auth, "_TOKEN_FILE_PATH", token_dir / "github_token.json")

    github_auth.set_token("octocat", "secret-token")

    mode = stat.S_IMODE(token_dir.stat().st_mode)
    assert mode == 0o700


def test_set_token_leaves_no_leftover_tmp_file(tmp_path, monkeypatch):
    token_path = tmp_path / "github_token.json"
    monkeypatch.setattr(github_auth, "_TOKEN_FILE_PATH", token_path)

    github_auth.set_token("octocat", "secret-token")

    assert not (tmp_path / "github_token.json.tmp").exists()


def test_set_token_tmp_file_is_restrictive_before_the_atomic_rename(tmp_path, monkeypatch):
    """Regression for the file being briefly group/world-readable: the old
    code did write_text() (created at the umask default) then chmod()
    afterwards, leaving a window where the plaintext token was exposed. The
    replacement file must already be mode 0600 at the moment it exists on
    disk, before os.replace() makes it visible as the real token file."""
    token_path = tmp_path / "github_token.json"
    monkeypatch.setattr(github_auth, "_TOKEN_FILE_PATH", token_path)
    captured_mode = {}
    original_replace = github_auth.os.replace

    def spy_replace(src, dst):
        captured_mode["mode"] = stat.S_IMODE(github_auth.os.stat(src).st_mode)
        return original_replace(src, dst)

    monkeypatch.setattr(github_auth.os, "replace", spy_replace)

    github_auth.set_token("octocat", "secret-token")

    assert captured_mode["mode"] == 0o600


def test_set_token_cleans_up_tmp_file_and_preserves_original_on_write_failure(
    tmp_path, monkeypatch
):
    token_path = tmp_path / "github_token.json"
    monkeypatch.setattr(github_auth, "_TOKEN_FILE_PATH", token_path)
    github_auth.set_token("octocat", "secret-token")

    class _ExplodingFile:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def write(self, _data):
            raise OSError("disk full")

    monkeypatch.setattr(github_auth.os, "fdopen", lambda fd, mode: _ExplodingFile())

    with pytest.raises(OSError):
        github_auth.set_token("octocat", "new-token")

    # The original file is untouched (no atomic replace happened) and the
    # temp file used for the failed write was cleaned up, not left behind.
    assert github_auth.get_token("octocat") == "secret-token"
    assert not (tmp_path / "github_token.json.tmp").exists()
