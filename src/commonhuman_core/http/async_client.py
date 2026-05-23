# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""
AsyncHttpClient — async HTTP session for CommonHuman-Lab scanners.

Drop-in async counterpart to HttpClient.  Uses httpx.AsyncClient instead of
requests.Session.  All injection helpers mirror the synchronous API but are
async def and must be awaited.

Usage inside an async context:
    client = AsyncHttpClient(timeout=15, proxy="http://127.0.0.1:8080")
    async with client:
        resp = await client.inject_get(url, "id", "1'")
    # or manually:
    resp = await client.inject_get(url, "id", "1'")
    await client.aclose()
"""

from __future__ import annotations

import asyncio
import urllib.parse as up
from typing import Any, Dict, List, Optional

import httpx

from .client import _UA_POOL, _RATE_LIMIT_BACKOFF, _RATE_LIMIT_RETRIES, random_ua
from ._cookies import parse_cookie_string

_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)


class AsyncHttpClient:
    """
    Async HTTP session with the same injection helper API as HttpClient.

    - All HTTP methods (get, post, head, inject_*) are coroutines.
    - request_count is thread-safe for asyncio (single-threaded event loop).
    - 429 back-off uses ``await asyncio.sleep()`` rather than blocking.
    - Static utilities (get_params, get_base_url, same_origin) are unchanged.
    """

    def __init__(
        self,
        timeout:    int = 15,
        proxy:      Optional[str] = None,
        headers:    Optional[Dict[str, str]] = None,
        cookies:    Optional[str] = None,
        verify_ssl: bool = False,
        delay:      float = 0.0,
        auth:       Any = None,
    ) -> None:
        self.timeout       = timeout
        self.request_count = 0
        self.delay         = max(0.0, delay)

        parsed_cookies = parse_cookie_string(cookies) if cookies else {}
        self._cookies_dict: Dict[str, str] = parsed_cookies

        base_headers: Dict[str, str] = {"User-Agent": random_ua()}
        if headers:
            base_headers.update(headers)

        client_kwargs: Dict[str, Any] = {
            "headers":    base_headers,
            "cookies":    parsed_cookies,
            "verify":     verify_ssl,
            "timeout":    timeout,
            "limits":     _DEFAULT_LIMITS,
            "follow_redirects": True,
        }
        if proxy:
            client_kwargs["proxy"] = proxy
        if auth is not None:
            client_kwargs["auth"] = auth

        self._client = httpx.AsyncClient(**client_kwargs)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "AsyncHttpClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Core HTTP
    # ------------------------------------------------------------------

    async def get(
        self,
        url: str,
        params: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.request_count += 1
        resp = await self._client.get(url, params=params, **kwargs)
        return await self._handle_rate_limit(
            resp,
            lambda: self._client.get(url, params=params, **kwargs),
        )

    async def post(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        if self.delay:
            await asyncio.sleep(self.delay)
        self.request_count += 1
        resp = await self._client.post(url, data=data, json=json_body, **kwargs)
        return await self._handle_rate_limit(
            resp,
            lambda: self._client.post(url, data=data, json=json_body, **kwargs),
        )

    async def head(self, url: str, **kwargs: Any) -> httpx.Response:
        self.request_count += 1
        return await self._client.head(url, **kwargs)

    async def _handle_rate_limit(
        self,
        resp: httpx.Response,
        retry_fn: Any,
    ) -> httpx.Response:
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
            await asyncio.sleep(wait)
            self.request_count += 1
            resp = await retry_fn()
        return resp

    # ------------------------------------------------------------------
    # Injection helpers
    # ------------------------------------------------------------------

    async def inject_get(self, url: str, param: str, payload: str) -> httpx.Response:
        parsed = up.urlparse(url)
        qs = up.parse_qs(parsed.query, keep_blank_values=True)
        qs[param] = [payload]
        target = up.urlunparse(parsed._replace(query=up.urlencode(qs, doseq=True)))
        return await self.get(target)

    async def inject_post(
        self,
        url: str,
        param: str,
        payload: str,
        base_data: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        data = dict(base_data or {})
        data[param] = payload
        return await self.post(url, data=data)

    async def inject_post_json(
        self,
        url: str,
        param: str,
        payload: str,
        base_data: Optional[Dict[str, Any]] = None,
    ) -> httpx.Response:
        body = dict(base_data or {})
        body[param] = payload
        return await self.post(url, json_body=body)

    async def inject_path(self, url: str, segment_index: int, payload: str) -> httpx.Response:
        parsed = up.urlparse(url)
        parts  = parsed.path.split("/")
        if segment_index == -1:
            parts.append(up.quote(str(payload), safe=""))
        elif 0 <= segment_index < len(parts):
            parts[segment_index] = up.quote(str(payload), safe="")
        target = up.urlunparse(parsed._replace(path="/".join(parts)))
        return await self.get(target)

    async def inject_cookie(self, url: str, cookie_name: str, payload: str) -> httpx.Response:
        return await self.get(url, cookies={cookie_name: payload})

    async def inject_header(self, url: str, header_name: str, payload: str) -> httpx.Response:
        return await self.get(url, headers={header_name: payload})

    # ------------------------------------------------------------------
    # URL utilities (identical to HttpClient — static, no async needed)
    # ------------------------------------------------------------------

    @staticmethod
    def get_params(url: str) -> List[str]:
        return list(up.parse_qs(up.urlparse(url).query, keep_blank_values=True).keys())

    @staticmethod
    def get_base_url(url: str) -> str:
        p = up.urlparse(url)
        return f"{p.scheme}://{p.netloc}"

    @staticmethod
    def same_origin(url_a: str, url_b: str) -> bool:
        pa, pb = up.urlparse(url_a), up.urlparse(url_b)
        return pa.scheme == pb.scheme and pa.netloc == pb.netloc

    async def aclose(self) -> None:
        await self._client.aclose()
