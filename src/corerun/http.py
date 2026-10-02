"""How an answer from the platform becomes a result, or a sentence.

Every HTTP answer the SDK or the CLI receives comes through decode_response.
It used to be read in a dozen places, each its own way: a 2xx was decoded as
JSON without looking, so a deploy in progress -- an empty body, a gateway's
HTML page -- reached the user as `Expecting value: line 1 column 1 (char 0)`,
which names nothing that happened; and a 401 said whatever the transport said,
which was never "sign in again".
"""

from typing import Any

from .exceptions import (
    AuthenticationError,
    ConflictError,
    CoreRunError,
    NotFoundError,
    RateLimitError,
    ServerError,
    ValidationError,
)

SIGN_IN_HINT = "Not signed in, or the session has expired. Run 'corerun login'."
RETRY_HINT = "If a deploy is in progress, try again in a moment."


def first_line(text: str, limit: int = 160) -> str:
    """The first non-empty line of a body, cut to a length a terminal forgives."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


def describe_body(response) -> str:
    """What a body that is not JSON is, in words a person can act on.

    An HTML page is named as one -- a gateway or an edge answering in place of
    the platform -- with its title when it has one, rather than quoted as a
    line of markup.
    """
    text = getattr(response, "text", "") or ""
    head = text[:400].lower()
    headers = getattr(response, "headers", None) or {}
    # Cloudflare's bot protection answers in the platform's place: a page a
    # browser solves and a program cannot. Naming it sends somebody to the
    # right dashboard instead of the platform's logs, which never saw it.
    if (headers.get("cf-mitigated") or "").lower() == "challenge" or "<title>just a moment" in head:
        return ("a Cloudflare challenge page -- Cloudflare's bot protection stopped the request "
                "before it reached the platform; an administrator has to exempt API traffic "
                "from it (Security → Bots, or a WAF skip rule)")
    if head.lstrip().startswith("<") and ("<html" in head or "<!doctype" in head):
        title = ""
        start, end = head.find("<title>"), head.find("</title>")
        if 0 <= start < end:
            title = text[start + 7 : end].strip()
        return f"an HTML page{f' titled {title!r}' if title else ''}"
    line = first_line(text)
    return repr(line) if line else "an empty body"


def error_detail(response) -> str:
    """The server's own sentence for a failure, whichever service wrote it.

    The Go API answers {"error": "<code>", "message": "<what happened>"}, the
    data service FastAPI's {"detail": "..."}, the authorization server OAuth's
    {"error", "error_description"}. The sentence wins over the code; a code
    beats nothing; and a body that is not JSON is described rather than pasted.
    """
    try:
        data = response.json()
    except Exception:
        return describe_body(response)
    if not isinstance(data, dict):
        return first_line(str(data))
    return (
        data.get("detail")
        or data.get("message")
        or data.get("error_description")
        or data.get("error")
        or str(data)
    )


def decode_response(response, *, as_text: bool = False) -> Any:
    """The body a success carries, decoded, or the error the status stands for.

    Args:
        response: an httpx.Response, or anything with status_code, content,
            text and json().
        as_text: return the body as it is; a route that serves a file answers
            with the file, and decoding that as JSON fails on line one.
    """
    status = response.status_code
    if 200 <= status < 300:
        if as_text:
            return response.text
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            headers = getattr(response, "headers", {}) or {}
            content_type = headers.get("content-type", "") if hasattr(headers, "get") else ""
            raise CoreRunError(
                f"The platform answered {status} but not with JSON"
                f" ({content_type or 'no content type'}): {describe_body(response)}. {RETRY_HINT}"
            )

    detail = error_detail(response)
    reason = getattr(response, "reason_phrase", "") or ""

    if status == 401:
        if detail in ("", "an empty body"):
            raise AuthenticationError(SIGN_IN_HINT)
        raise AuthenticationError(f"{detail}. {SIGN_IN_HINT}")
    if status == 404:
        raise NotFoundError(detail)
    if status in (400, 422):
        raise ValidationError(detail)
    if status == 409:
        code = ""
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("error"), str):
                code = body["error"]
        except Exception:
            pass
        raise ConflictError(detail, code=code)
    if status == 429:
        raise RateLimitError(detail)
    if status in (502, 503, 504):
        where = f"{status} {reason}".rstrip()
        raise ServerError(f"The platform is not answering ({where}): {detail}. {RETRY_HINT}")
    if status >= 500:
        raise ServerError(detail)
    raise CoreRunError(f"HTTP {status}: {detail}")
