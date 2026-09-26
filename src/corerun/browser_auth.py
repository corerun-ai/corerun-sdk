"""
Browser sign-in for a terminal that can open one.

The CLI listens on a loopback port, sends the browser to the platform's sign-in
page, and receives an authorization code back on that port. Nothing secret
travels through the browser: the code is useless without a verifier the CLI
never sent anywhere, which is what makes this safe on a machine where other
programs can see the URLs being opened.

Where there is no browser to open -- an SSH session, a notebook terminal --
device_auth.py handles it instead.
"""

import base64
import hashlib
import http.server
import os
import secrets
import socket
import threading
import urllib.parse
from typing import Optional, Tuple

import httpx

from corerun.config import DEFAULT_API_URL  # noqa: F401

# How long to wait for someone to finish signing in. Long enough to find a
# password manager, short enough that an abandoned attempt does not leave a
# listener open on the machine.
LOGIN_TIMEOUT_SECONDS = 300


class BrowserAuthError(Exception):
    """Browser sign-in could not be completed."""


def _pkce_pair() -> Tuple[str, str]:
    """A verifier and the challenge derived from it (RFC 7636)."""
    # 32 random bytes is 43 base64url characters, the minimum the RFC allows
    # and comfortably beyond guessing.
    verifier = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


class _Catcher(http.server.BaseHTTPRequestHandler):
    """Receives the redirect and holds what it carried."""

    result: dict = {}
    expected_state: str = ""

    def do_GET(self):  # noqa: N802 - name fixed by http.server
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        state = (params.get("state") or [""])[0]
        code = (params.get("code") or [""])[0]
        error = (params.get("error") or [""])[0]

        # The state ties this callback to the request this process started. A
        # browser can be sent to a loopback port by any page on the machine;
        # without this check, one of them could hand us a code of its own.
        if state != _Catcher.expected_state:
            _Catcher.result = {"error": "state mismatch; ignoring this callback"}
            self._reply("Sign-in could not be verified. You can close this window.")
            return

        _Catcher.result = {"error": error} if error else {"code": code}
        self._reply(
            "Signed in. You can close this window and return to your terminal."
            if code
            else "Sign-in was not completed. You can close this window."
        )

    def _reply(self, message: str):
        body = (
            "<!doctype html><meta charset=utf-8>"
            "<title>corerun</title>"
            "<body style=\"font:15px system-ui;margin:4rem auto;max-width:32rem;color:#111\">"
            f"<p>{message}</p></body>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        """Silence the default stderr logging; this is a UI, not a server."""


def available() -> bool:
    """Whether opening a browser is likely to work."""
    import webbrowser

    # A display-less machine reports no usable browser; SSH sessions set
    # SSH_CONNECTION and should use the device flow even if a browser exists,
    # because it would open on the wrong computer.
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    try:
        return webbrowser.get() is not None
    except Exception:
        return False


def login(
    api_url: str,
    web_url: str,
    client_name: Optional[str] = None,
    verify_ssl: bool = True,
    on_url=None,
) -> dict:
    """
    Sign in through the browser and return the issued tokens.

    on_url is called with the URL being opened, so the caller can print it --
    opening a browser fails silently often enough that the address has to be
    visible either way.

    Returns the token response: access_token, refresh_token, expires_at.
    """
    import webbrowser

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    client_name = client_name or socket.gethostname()

    # Port 0 asks the operating system for a free one, so two terminals signing
    # in at once do not collide.
    server = http.server.HTTPServer(("127.0.0.1", 0), _Catcher)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/callback"

    _Catcher.result = {}
    _Catcher.expected_state = state

    url = authorize_url(web_url, redirect_uri, challenge, state, client_name)
    if on_url:
        on_url(url)

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    try:
        webbrowser.open(url)
    except Exception:
        # The URL was printed; a browser that will not open is not fatal.
        pass

    thread.join(timeout=LOGIN_TIMEOUT_SECONDS)
    server.server_close()

    if not _Catcher.result:
        raise BrowserAuthError(
            "timed out waiting for the browser. If this machine has no browser, "
            "run: corerun login --use-device-code"
        )
    if "error" in _Catcher.result:
        raise BrowserAuthError(_Catcher.result["error"] or "sign-in was denied")

    code = _Catcher.result.get("code")
    if not code:
        raise BrowserAuthError("no authorization code was returned")

    return exchange(api_url, code, verifier, redirect_uri, verify_ssl=verify_ssl)


def authorize_url(web_url: str, redirect_uri: str, challenge: str, state: str, client_name: str) -> str:
    """The URL to open. Exposed so a caller can print it when opening fails."""
    params = urllib.parse.urlencode(
        {
            "redirect_uri": redirect_uri,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "client_name": client_name,
        }
    )
    return f"{web_url.rstrip('/')}/cli-auth?{params}"


def exchange(
    api_url: str,
    code: str,
    verifier: str,
    redirect_uri: str,
    verify_ssl: bool = True,
) -> dict:
    """Trade the authorization code for tokens."""
    try:
        response = httpx.post(
            f"{api_url.rstrip('/')}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri,
            },
            timeout=30,
            verify=verify_ssl,
        )
    except httpx.HTTPError as exc:
        raise BrowserAuthError(f"could not reach {api_url}: {exc}") from exc

    if response.status_code != 200:
        from corerun.http import error_detail

        raise BrowserAuthError(f"could not exchange the authorization code: {error_detail(response)}")

    return response.json()
