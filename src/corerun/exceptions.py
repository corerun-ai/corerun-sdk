"""
corerun SDK exceptions.
"""

import socket
from urllib.parse import urlsplit


class CoreRunError(Exception):
    """Base exception for corerun SDK"""
    pass


class AuthenticationError(CoreRunError):
    """Authentication failed (401)"""
    pass


class NotFoundError(CoreRunError):
    """Resource not found (404)"""
    pass


class ValidationError(CoreRunError):
    """Request validation failed (400, 422)"""
    pass


class ConflictError(CoreRunError):
    """The request conflicts with the resource's state (409).

    ``code`` is the platform's name for the conflict -- ``restart_required``
    when a running notebook cannot change what it mounts -- so a caller can
    act on it rather than on the wording.
    """

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


class RateLimitError(CoreRunError):
    """Rate limit exceeded (429)"""
    pass


class ServerError(CoreRunError):
    """Server error (5xx)"""
    pass


class TimeoutError(CoreRunError):
    """Request timed out"""
    pass


class ConfigurationError(CoreRunError):
    """Configuration error"""
    pass


class UnreachableError(CoreRunError):
    """Nothing answered at the address the request was going to."""
    pass


# How the resolver phrases "that name has no address". Matched on text as well
# as on the exception type below, because the type is what carries it today and
# the wording is what a caller sees when a layer in between rewrites it.
_DNS_FAILURES = (
    "nodename nor servname",         # macOS and the BSDs
    "name or service not known",     # Linux
    "temporary failure in name resolution",
    "getaddrinfo failed",
    "no address associated",
)


def _is_name_failure(error: BaseException) -> bool:
    """Whether the name never resolved, rather than nobody being home.

    The two are worth telling apart because they need different answers: a name
    with no address is the wrong address or a network that cannot see the right
    one, while a refused connection is a server that is down.
    """
    node, depth = error, 0
    while node is not None and depth < 8:
        if isinstance(node, socket.gaierror):
            return True
        node = node.__cause__ or node.__context__
        depth += 1

    message = str(error).lower()
    return any(phrase in message for phrase in _DNS_FAILURES)


def unreachable(
    url: str, error: BaseException, setting: str = "CORERUN_API_URL"
) -> UnreachableError:
    """Describe a request that was never sent, naming the address.

    A resolver failure reaches the user as the operating system worded it --
    "[Errno 8] nodename nor servname provided, or not known" -- which names
    neither the host nor anything to do about it. The address is the whole of
    what is wrong, so it goes in the message, together with the setting that
    chose it, which is the thing to change.

    Args:
        url: the address the request was going to
        error: what the transport raised
        setting: the environment variable that overrides this address
    """
    host = urlsplit(url).netloc or url

    if _is_name_failure(error):
        return UnreachableError(
            f"cannot resolve {host}: no address for that name. Check the network "
            f"this machine is on, or set {setting} to the address your deployment "
            f"is served at."
        )

    return UnreachableError(f"cannot reach {url}: {error}")
