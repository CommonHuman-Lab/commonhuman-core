# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""
commonhuman-core — auth.py
Form-based and token-based authentication helpers.

Provides:
  - form_login()   — POST credentials to an HTML login form, return session cookies/headers
  - bearer_login() — OAuth 2.0 client credentials grant, return Authorization header
  - extract_csrf() — pull a CSRF token from an HTML page
  - AuthResult     — carries cookies (str) + headers (dict) ready for subsequent requests
"""
from __future__ import annotations

import json
import logging
import urllib.parse as up
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

from .http.client import HttpClient

logger = logging.getLogger(__name__)

_CSRF_NAMES = frozenset({
    "csrf_token", "_token", "xsrf_token", "authenticity_token",
    "csrfmiddlewaretoken", "_csrf", "csrf", "__requestverificationtoken",
})


# ---------------------------------------------------------------------------
# Public data type
# ---------------------------------------------------------------------------

@dataclass
class AuthResult:
    """Session credentials ready to pass to any CommonHuman-Lab scanner."""
    cookies: str = ""
    headers: Dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.cookies and not self.headers


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def form_login(
    login_url:      str,
    username:       str,
    password:       str,
    username_field: str = "username",
    password_field: str = "password",
    extra_fields:   Optional[Dict[str, str]] = None,
    client:         Optional[HttpClient] = None,
    timeout:        int = 15,
) -> AuthResult:
    """Submit an HTML login form and return the resulting session.

    Fetches the login page, extracts CSRF tokens and hidden fields, POSTs
    credentials to the form action, then collects session cookies.  If the
    server responds with a JSON body containing a token field the Bearer
    header is populated automatically.

    Returns an empty AuthResult on network or parse failure.
    """
    c = client or HttpClient(timeout=timeout)

    try:
        resp = c._session.get(login_url, timeout=timeout)
    except Exception as exc:
        logger.warning("form_login: GET %s failed: %s", login_url, exc)
        return AuthResult()

    parser = _FormParser()
    parser.feed(resp.text)

    action = parser.action or login_url
    if action and not action.startswith(("http://", "https://")):
        action = up.urljoin(login_url, action)

    body: Dict[str, str] = dict(parser.fields)
    body[username_field] = username
    body[password_field] = password
    if extra_fields:
        body.update(extra_fields)

    try:
        post_resp = c._session.post(action, data=body, timeout=timeout, allow_redirects=True)
    except Exception as exc:
        logger.warning("form_login: POST %s failed: %s", action, exc)
        return AuthResult()

    cookies = "; ".join(
        f"{name}={val}"
        for name, val in c._session.cookies.items()
    )

    result_headers: Dict[str, str] = {}
    try:
        j = post_resp.json()
        for key in ("token", "access_token", "accessToken", "jwt", "id_token"):
            if key in j and isinstance(j[key], str):
                result_headers["Authorization"] = f"Bearer {j[key]}"
                break
    except Exception:
        pass

    result = AuthResult(cookies=cookies, headers=result_headers)
    if result.is_empty():
        logger.warning("form_login: no cookies or token obtained from %s", login_url)
    else:
        logger.info("form_login: authenticated via %s (%d cookies)", login_url, cookies.count(";") + 1)
    return result


def bearer_login(
    token_url:     str,
    client_id:     str,
    client_secret: str,
    grant_type:    str = "client_credentials",
    client:        Optional[HttpClient] = None,
    timeout:       int = 15,
) -> AuthResult:
    """OAuth 2.0 token endpoint — client credentials or password grant.

    Returns AuthResult with Authorization: Bearer <token> header populated,
    or empty AuthResult on failure.
    """
    c = client or HttpClient(timeout=timeout)
    body = {
        "grant_type":    grant_type,
        "client_id":     client_id,
        "client_secret": client_secret,
    }
    try:
        resp = c._session.post(token_url, data=body, timeout=timeout)
        j = resp.json()
        token = j.get("access_token") or j.get("token") or j.get("id_token")
        if token:
            logger.info("bearer_login: obtained token from %s", token_url)
            return AuthResult(headers={"Authorization": f"Bearer {token}"})
    except Exception as exc:
        logger.warning("bearer_login: %s failed: %s", token_url, exc)
    return AuthResult()


def http_auth(auth_type: str, cred: str) -> Any:
    """Return a requests-compatible auth object for Basic, Digest, or NTLM auth.

    Args:
        auth_type: ``"basic"``, ``"digest"``, or ``"ntlm"``.
        cred:      Credentials in ``"username:password"`` format. The password
                   may itself contain colons — only the first colon is used as
                   the delimiter.

    Returns:
        A ``requests.auth.HTTPBasicAuth``, ``requests.auth.HTTPDigestAuth``,
        or ``requests_ntlm.HttpNtlmAuth`` instance ready to be passed to
        ``HttpClient(auth=...)``.

    Raises:
        ValueError:  Invalid *auth_type* or malformed *cred*.
        ImportError: ``auth_type="ntlm"`` requested but ``requests-ntlm`` is
                     not installed (``pip install commonhuman-core[ntlm]``).
    """
    if not cred or ":" not in cred:
        raise ValueError(
            f"auth_cred must be in 'username:password' format, got {cred!r}"
        )
    user, _, password = cred.partition(":")

    if auth_type == "basic":
        from requests.auth import HTTPBasicAuth
        return HTTPBasicAuth(user, password)
    if auth_type == "digest":
        from requests.auth import HTTPDigestAuth
        return HTTPDigestAuth(user, password)
    if auth_type == "ntlm":
        try:
            from requests_ntlm import HttpNtlmAuth  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "NTLM auth requires requests-ntlm: pip install commonhuman-core[ntlm]"
            ) from exc
        return HttpNtlmAuth(user, password)
    raise ValueError(
        f"Unknown auth_type {auth_type!r}. Supported values: basic, digest, ntlm"
    )


def extract_csrf(html: str) -> Optional[str]:
    """Extract a CSRF token from an HTML page.

    Scans for ``<input type="hidden">`` elements whose name matches known
    CSRF field name patterns.  Returns the first value found, or None.
    """
    parser = _FormParser()
    parser.feed(html)
    for name, value in parser.fields.items():
        if name.lower() in _CSRF_NAMES and value:
            return value
    return None


# ---------------------------------------------------------------------------
# Internal HTML form parser
# ---------------------------------------------------------------------------

class _FormParser(HTMLParser):
    """Minimal parser that extracts the first HTML form's action and fields."""

    def __init__(self) -> None:
        super().__init__()
        self.action: str = ""
        self.method: str = "post"
        self.fields: Dict[str, str] = {}
        self._in_form: bool = False
        self._done: bool = False

    def handle_starttag(self, tag: str, attrs: List) -> None:
        if self._done:
            return
        a = dict(attrs)
        if tag == "form" and not self._in_form:
            self._in_form = True
            self.action = a.get("action", "")
            self.method = a.get("method", "post").lower()
        elif tag == "input" and self._in_form:
            name  = a.get("name", "")
            value = a.get("value") or ""
            itype = a.get("type", "text").lower()
            if name and itype not in ("submit", "button", "image", "reset", "file"):
                self.fields[name] = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._in_form:
            self._in_form = False
            self._done = True
