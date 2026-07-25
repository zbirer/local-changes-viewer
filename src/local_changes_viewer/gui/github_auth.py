import keyring

_KEYRING_SERVICE = "local-changes-viewer-github"


def get_token(username: str) -> str | None:
    return keyring.get_password(_KEYRING_SERVICE, username)


def set_token(username: str, token: str) -> None:
    keyring.set_password(_KEYRING_SERVICE, username, token)


def delete_token(username: str) -> None:
    try:
        keyring.delete_password(_KEYRING_SERVICE, username)
    except keyring.errors.PasswordDeleteError:
        pass
