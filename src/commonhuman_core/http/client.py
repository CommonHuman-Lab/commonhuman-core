# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""
HttpClient — shared HTTP session for CommonHuman-Lab scanners.
"""

from __future__ import annotations

import random
import time
import urllib.parse as up
from typing import Any, Dict, List, Optional

import urllib3
import requests
from requests import Response
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ._cookies import parse_cookie_string

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_UA_POOL: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
]

# Kept for backwards compatibility — prefer random_ua() for new code.
DEFAULT_UA = _UA_POOL[0]


def random_ua() -> str:
    """Return a random realistic browser User-Agent string."""
    return random.choice(_UA_POOL)

_RATE_LIMIT_BACKOFF  = 5.0  # seconds to wait on 429
_RATE_LIMIT_RETRIES  = 2    # max retries on 429


class HttpClient:
    """
    Thin wrapper around ``requests.Session`` providing:

    - Configurable proxy, headers, cookies, SSL verification
    - Automatic retry on transient connection/read errors
    - 429 rate-limit back-off with ``Retry-After`` header support
    - Per-request delay (rate throttling)
    - Request counter (for scan result reporting)
    - Injection helpers for GET params, POST body, JSON body, path
      segments, cookies, and custom headers
    """

    def __init__(
        self,
        timeout:    int = 15,
        proxy:      Optional[str] = None,
        headers:    Optional[Dict[str, str]] = None,
        cookies:    Optional[str] = None,
        verify_ssl: bool = False,
        delay:      float = 0.0,
    ) -> None:
        self.timeout       = timeout
        self.request_count = 0
        self.delay         = max(0.0, delay)

        self._session = requests.Session()
        self._session.verify = verify_ssl

        retry = Retry(
            total=2,
            backoff_factor=0.3,
            status_forcelist=(),
            allowed_methods=["GET", "POST", "HEAD"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://",  adapter)
        self._session.mount("https://", adapter)

        base_headers: Dict[str, str] = {"User-Agent": random_ua()}
        if headers:
            base_headers.update(headers)
        self._session.headers.update(base_headers)

        if cookies:
            self._session.cookies.update(parse_cookie_string(cookies))

        if proxy:
            self._session.proxies = {"http": proxy, "https": proxy}

    # ------------------------------------------------------------------
    # Core HTTP
    # ------------------------------------------------------------------

    def get(self, url: str, params: Optional[Dict[str, str]] = None, **kwargs) -> Response:
        if self.delay:
            time.sleep(self.delay)
        self.request_count += 1
        resp = self._session.get(url, params=params, timeout=self.timeout, **kwargs)
        return self._handle_rate_limit(
            resp,
            lambda: self._session.get(url, params=params, timeout=self.timeout, **kwargs),
        )

    def post(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        **kwargs,
    ) -> Response:
        if self.delay:
            time.sleep(self.delay)
        self.request_count += 1
        resp = self._session.post(url, data=data, json=json_body, timeout=self.timeout, **kwargs)
        return self._handle_rate_limit(
            resp,
            lambda: self._session.post(
                url, data=data, json=json_body, timeout=self.timeout, **kwargs
            ),
        )

    def head(self, url: str, **kwargs) -> Response:
        self.request_count += 1
        return self._session.head(url, timeout=self.timeout, allow_redirects=True, **kwargs)

    def _handle_rate_limit(self, resp: Response, retry_fn) -> Response:
        """Back off and retry when the server returns HTTP 429."""
        for _ in range(_RATE_LIMIT_RETRIES):
            if resp.status_code != 429:
                break
            wait = _RATE_LIMIT_BACKOFF
            retry_after = resp.headers.get("Retry-After", "")
            if retry_after:
                try:
                    wait = max(float(retry_after), _RATE_LIMIT_BACKOFF)
                except ValueError:
                    pass
            time.sleep(wait)
            self.request_count += 1
            resp = retry_fn()
        return resp

    # ------------------------------------------------------------------
    # Injection helpers
    # ------------------------------------------------------------------

    def inject_get(self, url: str, param: str, payload: str) -> Response:
        """Replace the value of ``param`` in the URL query string with ``payload``."""
        parsed = up.urlparse(url)
        qs = up.parse_qs(parsed.query, keep_blank_values=True)
        qs[param] = [payload]
        target = up.urlunparse(parsed._replace(query=up.urlencode(qs, doseq=True)))
        return self.get(target)

    def inject_post(
        self,
        url: str,
        param: str,
        payload: str,
        base_data: Optional[Dict[str, str]] = None,
    ) -> Response:
        """Replace the value of ``param`` in a POST form body with ``payload``."""
        data = dict(base_data or {})
        data[param] = payload
        return self.post(url, data=data)

    def inject_post_json(
        self,
        url: str,
        param: str,
        payload: str,
        base_data: Optional[Dict[str, Any]] = None,
    ) -> Response:
        """Replace the value of ``param`` in a JSON POST body with ``payload``."""
        body = dict(base_data or {})
        body[param] = payload
        return self.post(url, json_body=body)

    def inject_path(self, url: str, segment_index: int, payload: str) -> Response:
        """Replace the path segment at ``segment_index`` (0-based) with ``payload``.

        Useful for REST-style path parameters such as ``/api/user/:id``.
        Pass ``-1`` to append as a new trailing segment.
        """
        parsed = up.urlparse(url)
        parts  = parsed.path.split("/")
        if segment_index == -1:
            parts.append(up.quote(str(payload), safe=""))
        elif 0 <= segment_index < len(parts):
            parts[segment_index] = up.quote(str(payload), safe="")
        target = up.urlunparse(parsed._replace(path="/".join(parts)))
        return self.get(target)

    def inject_cookie(self, url: str, cookie_name: str, payload: str) -> Response:
        """Override ``cookie_name`` with ``payload`` for this single request."""
        return self.get(url, cookies={cookie_name: payload})

    def inject_header(self, url: str, header_name: str, payload: str) -> Response:
        """Send ``payload`` as the value of ``header_name`` for this single request."""
        return self.get(url, headers={header_name: payload})

    # ------------------------------------------------------------------
    # URL utilities
    # ------------------------------------------------------------------

    @staticmethod
    def get_params(url: str) -> List[str]:
        """Return query parameter names from ``url``."""
        return list(up.parse_qs(up.urlparse(url).query, keep_blank_values=True).keys())

    @staticmethod
    def get_base_url(url: str) -> str:
        """Return ``scheme://netloc`` from ``url``."""
        p = up.urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    @staticmethod
    def same_origin(url_a: str, url_b: str) -> bool:
        """Return ``True`` if both URLs share the same scheme and netloc."""
        pa, pb = up.urlparse(url_a), up.urlparse(url_b)
        return pa.scheme == pb.scheme and pa.netloc == pb.netloc

    def close(self) -> None:
        self._session.close()
