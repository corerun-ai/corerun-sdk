"""
corerun HTTP client.

Handles authentication, request/response, and error handling.
"""

import os
from typing import Any, Callable, Dict, Optional, TypeVar

import httpx
from pydantic import BaseModel

from corerun.config import Config
from corerun.credentials import read_platform_credential
from corerun.exceptions import (
    AuthenticationError,
    unreachable,
)
from corerun.http import decode_response

T = TypeVar("T", bound=BaseModel)


class CoreRunClient:
    """
    HTTP client for corerun API.

    Handles authentication, retries, and error handling.
    """

    def __init__(self, config: Config):
        self.config = config
        self._client: Optional[httpx.Client] = None

    @property
    def client(self) -> httpx.Client:
        """Get or create HTTP client"""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.config.api_url,
                timeout=self.config.timeout,
                headers=self._default_headers(),
                verify=self.config.verify_ssl,
            )
        return self._client

    def _default_headers(self) -> Dict[str, str]:
        """Default request headers"""
        headers = {
            "Authorization": f"Bearer {self.config.auth_token}",
            "Content-Type": "application/json",
            "User-Agent": "corerun-python/0.1.0",
        }
        if self.config.workspace:
            headers["X-Workspace-ID"] = self.config.workspace
        return headers

    def _handle_response(self, response: httpx.Response, as_text: bool = False):
        """Handle API response and raise appropriate errors

        Returns the body as a string when as_text is set, and as decoded JSON
        otherwise. Only the success body changes: the API answers an error as
        JSON whatever the route serves on success, so the failure path below is
        the same either way.
        """
        return decode_response(response, as_text=as_text)

    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        workspace: Optional[str] = None,
        content_type: Optional[str] = None,
        response_type: str = "json",
        **kwargs,
    ):
        """
        Make an API request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API path (e.g., "/jobs")
            params: Query parameters
            json: JSON body
            workspace: Override workspace ID for this request
            content_type: What the body is, when it is not JSON. A document a
                person wrote may be YAML, and the default header would call it
                JSON -- which is a lie a server is entitled to believe.
            response_type: "json" (the default) or "text". A route that serves a
                manifest or a script answers with the file itself, and decoding
                that as JSON fails on the first line.
            kwargs: Passed through to the transport, so `content=` sends bytes
                this client does not interpret.

        Returns:
            Response JSON as dict, or the body as a string when response_type is
            "text"

        Raises:
            CoreRunError: On API errors
        """
        headers = {}
        if workspace:
            headers["X-Workspace-ID"] = workspace
        if content_type:
            headers["Content-Type"] = content_type

        response = self._send(method, path, params, json, headers, **kwargs)

        # A notebook's platform credential is replaced every few hours. A client
        # built before the swap holds the previous key, and the only way it
        # finds out is a 401 — so read the file again and retry once before
        # reporting an auth failure the user cannot act on.
        if response.status_code == 401 and self._refresh_credential():
            response = self._send(method, path, params, json, headers, **kwargs)

        return self._handle_response(response, as_text=(response_type == "text"))

    def _send(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]],
        json: Optional[Dict[str, Any]],
        headers: Dict[str, str],
        **kwargs,
    ) -> httpx.Response:
        """Make the request, or describe the address that could not be reached.

        A failure here is not the platform answering "no": it is nothing having
        answered at all, which the transport reports in whatever words the
        operating system used. Those words name no host and suggest nothing, so
        they are replaced with a sentence about the address.
        """
        try:
            return self.client.request(
                method=method,
                url=path,
                params=params,
                json=json,
                headers=headers,
                **kwargs,
            )
        except httpx.ConnectError as e:
            raise unreachable(f"{self.config.api_url.rstrip('/')}/{path.lstrip('/')}", e) from e

    def _refresh_credential(self) -> bool:
        """
        Get a working credential after a 401, returning True if one was found.

        Two ways a token goes stale, and they need different answers. Inside a
        notebook the platform rewrites a credential file every few hours, so
        re-reading it is enough. Everywhere else the token simply expired, and a
        refresh token is what buys another.

        False when neither applies, in which case the failure is real and
        retrying would only repeat it.
        """
        if self.config.auth_token_file:
            key = read_platform_credential()
            if key and key != self.config.auth_token:
                self._use_key(key)
                return True
            return False

        return self._exchange_refresh_token()

    def _exchange_refresh_token(self) -> bool:
        """
        Trade the refresh token for a new access token, and remember it.

        The new token is written back to the config file so the next command
        starts with a working one rather than paying for this round trip again.

        Never raises: this runs while handling a failure, and an error here
        would replace an honest "not authorised" with something unrelated.
        """
        if not self.config.refresh_token:
            return False

        try:
            response = httpx.post(
                f"{self.config.api_url}/auth/token/refresh",
                json={"refresh_token": self.config.refresh_token},
                timeout=self.config.timeout,
                verify=self.config.verify_ssl,
            )
        except Exception:
            return False

        if response.status_code != 200:
            # The refresh token is spent, revoked or expired. Signing in again
            # is the only way forward, and the caller is about to be told so.
            return False

        try:
            body = response.json()
        except Exception:
            return False

        token = body.get("access_token")
        if not token:
            return False

        # The refresh token is rotated: the one just spent will not work again,
        # and its replacement is in this response. Failing to store it is not a
        # missed optimisation -- presenting the spent one next time is how a
        # stolen token is detected, so the server ends the whole session and
        # the user is signed out for no reason they can see.
        replacement = body.get("refresh_token")
        if replacement:
            self.config.refresh_token = replacement

        self._use_key(token)
        try:
            self.config.save()
        except Exception:
            # Writing the file is what makes the rotation durable. If it fails
            # the process keeps working on the token in memory, but the next
            # one will present a spent refresh token, so say so rather than
            # letting the session end silently later.
            if replacement:
                import sys

                print(
                    "corerun: signed in, but could not write ~/.corerun/config. "
                    "Run `corerun login` again if the next command fails.",
                    file=sys.stderr,
                )
        return True

    def _use_key(self, key: str) -> None:
        self.config.auth_token = key
        if self._client is not None:
            self._client.headers["Authorization"] = f"Bearer {key}"

    def get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        workspace: Optional[str] = None,
        **kwargs,
    ):
        """Make a GET request.

        kwargs reach the transport, so a caller reading a route that serves a
        file rather than a document can pass `response_type="text"`.
        """
        return self.request("GET", path, params=params, workspace=workspace, **kwargs)

    def post(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        workspace: Optional[str] = None,
        **kwargs,
    ):
        """
        Make a POST request.

        Args:
            path: API path
            json: JSON body (mutually exclusive with files/data)
            files: Files to upload as multipart form data
            data: Form data fields (used with files)
            workspace: Override workspace ID
            kwargs: Passed through to request(), so `response_type="text"` reads
                a route whose success body is a manifest rather than JSON.
        """
        if files is not None:
            # Multipart form upload - need to handle differently
            return self._post_multipart(path, files=files, data=data, workspace=workspace)
        return self.request("POST", path, json=json, workspace=workspace, **kwargs)

    def download(
        self,
        path: str,
        dest: str,
        params: Optional[Dict[str, Any]] = None,
        workspace: Optional[str] = None,
        on_chunk: Optional[Callable[[int], None]] = None,
    ) -> int:
        """Stream a response body to a file, returning the bytes written.

        Not every response is JSON, and the ordinary path assumes one: it reads
        the whole body and parses it. A dataset file is bytes and can be large
        enough that holding it in memory on the way to disk is the difference
        between a download working and the process dying.

        Args:
            path: API path
            dest: File to write. Its directory is created if it does not exist.
            params: Query parameters
            workspace: Override workspace ID
            on_chunk: Called with the size of each chunk, for progress display
        """
        headers = {}
        if workspace:
            headers["X-Workspace-ID"] = workspace

        directory = os.path.dirname(dest)
        if directory:
            os.makedirs(directory, exist_ok=True)

        def fetch() -> int:
            written = 0
            # No timeout: the configured one is for a request that answers, and
            # this one answers by transferring for as long as the file takes.
            with self.client.stream(
                "GET", path, params=params, headers=headers, timeout=None
            ) as response:
                if response.status_code >= 300:
                    response.read()
                    self._handle_response(response)  # raises

                with open(dest, "wb") as out:
                    for chunk in response.iter_bytes():
                        out.write(chunk)
                        written += len(chunk)
                        if on_chunk is not None:
                            on_chunk(len(chunk))
            return written

        try:
            return fetch()
        except AuthenticationError:
            # The same credential rotation the ordinary path retries for.
            if not self._refresh_credential():
                raise
            return fetch()

    def _post_multipart(
        self,
        path: str,
        files: Dict[str, Any],
        data: Optional[Dict[str, Any]] = None,
        workspace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Make a POST request with multipart form data.

        Args:
            path: API path
            files: Files to upload {field_name: (filename, file_obj, content_type)}
            data: Form data fields
            workspace: Override workspace ID
        """
        headers = {
            "Authorization": f"Bearer {self.config.auth_token}",
            "User-Agent": "corerun-python/0.1.0",
        }
        ws = workspace or self.config.workspace
        if ws:
            headers["X-Workspace-ID"] = ws

        # Create a new client without default Content-Type header for multipart
        with httpx.Client(
            base_url=self.config.api_url,
            timeout=self.config.timeout,
            verify=self.config.verify_ssl,
        ) as client:
            try:
                response = client.post(
                    path,
                    files=files,
                    data=data or {},
                    headers=headers,
                )
            except httpx.ConnectError as e:
                raise unreachable(
                    f"{self.config.api_url.rstrip('/')}/{path.lstrip('/')}", e
                ) from e
            return self._handle_response(response)

    def put(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        workspace: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make a PUT request.

        kwargs reach the transport, so a caller with a body this client does not
        interpret -- a YAML document, say -- can pass `content=` and
        `content_type=`.
        """
        return self.request("PUT", path, json=json, workspace=workspace, **kwargs)

    def patch(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        workspace: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make a PATCH request.

        Here because the GenAI engine takes a trace's tags a pair at a time
        over PATCH, which is the one place this client needs the verb.
        """
        return self.request("PATCH", path, json=json, workspace=workspace, **kwargs)

    def delete(
        self,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        workspace: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Make a DELETE request.

        DELETE carries a body here because removing a cluster takes options --
        what to tear down along with it -- and the API reads them from one.
        """
        return self.request("DELETE", path, json=json, workspace=workspace, **kwargs)

    def close(self):
        """Close the HTTP client"""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
