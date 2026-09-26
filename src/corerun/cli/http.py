"""One way for a command to call the platform.

The commands that talk to the API without the SDK client -- workspaces,
groups, storage -- each had a copy of the same dozen lines, and each copy
decoded what came back on its own terms. This is the one copy: the address,
the credential, the answer decoded through corerun.http, a session refreshed
once when it has expired, and every failure an exception the entry point turns
into a sentence.
"""

from typing import Any, Dict, Optional

import httpx

from corerun.config import Config, get_config
from corerun.exceptions import AuthenticationError, TimeoutError, unreachable
from corerun.http import SIGN_IN_HINT, decode_response

# A test pins this to an httpx.MockTransport; nothing else sets it.
transport: Optional[httpx.BaseTransport] = None


def _send(
    method: str,
    url: str,
    headers: Dict[str, str],
    *,
    json: Any = None,
    data: Any = None,
    params: Any = None,
    timeout: float,
    verify: bool,
) -> httpx.Response:
    """The request, or the sentence for why it was never answered."""
    try:
        with httpx.Client(verify=verify, timeout=timeout, transport=transport) as client:
            return client.request(
                method, url, headers=headers, json=json, data=data, params=params
            )
    except httpx.ConnectError as e:
        raise unreachable(url, e) from e
    except httpx.TimeoutException as e:
        raise TimeoutError(f"{url} did not answer within {timeout:g}s: {e}") from e


def call(
    method: str,
    url: str,
    *,
    token: str,
    verify: bool = True,
    json: Any = None,
    data: Any = None,
    params: Any = None,
    timeout: float = 60.0,
    workspace: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    config: Optional[Config] = None,
    as_text: bool = False,
) -> Any:
    """Call an address with a credential and return the decoded answer.

    With a config, an answer of 401 is tried once more after the session has
    been refreshed from the refresh token -- an access token lasts an hour and
    the command a person runs an hour after signing in should not be the one
    that tells them to sign in again.
    """
    if not token:
        raise AuthenticationError(SIGN_IN_HINT)
    hdrs = {"Authorization": f"Bearer {token}", **(headers or {})}
    if workspace:
        hdrs["X-Workspace-ID"] = workspace

    send = lambda: _send(  # noqa: E731
        method, url, hdrs, json=json, data=data, params=params, timeout=timeout, verify=verify
    )
    response = send()
    if response.status_code == 401 and config is not None and refresh_session(config):
        hdrs["Authorization"] = f"Bearer {config.auth_token}"
        response = send()
    return decode_response(response, as_text=as_text)


def request(
    method: str,
    path: str,
    *,
    workspace: bool = False,
    json: Any = None,
    data: Any = None,
    params: Any = None,
    timeout: float = 60.0,
    headers: Optional[Dict[str, str]] = None,
    as_text: bool = False,
) -> Any:
    """Call the platform this CLI is signed in to, at a path under its API.

    Args:
        workspace: send the selected workspace's id, for a route that acts in
            one. A command that needs a workspace and has none selected is
            told to select one rather than answered by the platform's own
            refusal.
    """
    config = get_config()
    if not config.auth_token:
        raise AuthenticationError(SIGN_IN_HINT)
    ws = None
    if workspace:
        if not config.workspace:
            raise AuthenticationError("No workspace selected. Run 'corerun workspace set' first.")
        ws = config.workspace
    if path.startswith(("http://", "https://")):
        url = path
    else:
        url = config.api_url.rstrip("/") + "/" + path.lstrip("/")
    return call(
        method,
        url,
        token=config.auth_token,
        verify=config.verify_ssl,
        json=json,
        data=data,
        params=params,
        timeout=timeout,
        workspace=ws,
        headers=headers,
        config=config,
        as_text=as_text,
    )


def refresh_session(config: Config) -> bool:
    """Trade the refresh token for a new access token, and remember it.

    The SDK client already knows how -- and writes the rotated refresh token
    back, which is what keeps a session alive rather than ending it the next
    time. Never raises: this runs while handling a 401, and an error here
    would replace an honest "sign in again" with something unrelated.
    """
    if not config.refresh_token:
        return False
    try:
        from corerun.client import CoreRunClient

        return bool(CoreRunClient(config)._exchange_refresh_token())
    except Exception:
        return False
