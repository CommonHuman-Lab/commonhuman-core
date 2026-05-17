# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Multi-engine URL discovery (dorking).

Supports DuckDuckGo, Bing, and Yahoo.  Each engine fetches HTML results and
returns URLs that carry query parameters — the candidates most likely to have
injectable surfaces.  No API keys required.

Usage::

    from commonhuman_core.dorker import dork, DorkEngine

    urls = dork("site:example.com inurl:search", engine=DorkEngine.ALL)
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

import requests

__all__ = ["DorkEngine", "dork"]


class DorkEngine:
    """String constants identifying supported search engines."""
    DDG   = "ddg"
    BING  = "bing"
    YAHOO = "yahoo"
    ALL   = "all"


_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# DuckDuckGo
_DDG_URL  = "https://html.duckduckgo.com/html/"
_DDG_RE   = re.compile(r'uddg=([^&"\s]+)')

# Bing
_BING_URL = "https://www.bing.com/search"
_BING_RE  = re.compile(r'<a[^>]+href="(https?://[^"]+)"[^>]*class="[^"]*tilk[^"]*"', re.IGNORECASE)
_BING_RE2 = re.compile(r'<cite[^>]*>(https?://[^<]+)</cite>', re.IGNORECASE)

# Yahoo
_YAHOO_URL = "https://search.yahoo.com/search"
_YAHOO_RE  = re.compile(r'<a[^>]+href="(https?://[^"&]+)"[^>]*><b>', re.IGNORECASE)
_YAHOO_RU  = re.compile(r'/RU=(https?://[^/]+(?:/[^/]*)*)/RK=', re.IGNORECASE)


def dork(
    query:       str,
    max_results: int = 20,
    proxy:       str = "",
    timeout:     int = 15,
    engine:      str = DorkEngine.DDG,
) -> list[str]:
    """Query one or more search engines and return URLs with query parameters.

    Results are returned deduplicated, in result-rank order.  Only ``http``
    and ``https`` URLs with at least one query parameter are included — these
    are the most likely candidates for parameter-injection testing.

    Args:
        query:       Search query (e.g. ``'site:example.com inurl:search'``).
        max_results: Maximum number of URLs to return per engine (default 20).
        proxy:       Optional HTTP proxy URL (e.g. ``'http://127.0.0.1:8080'``).
        timeout:     Request timeout in seconds (default 15).
        engine:      One of ``DorkEngine.DDG``, ``DorkEngine.BING``,
                     ``DorkEngine.YAHOO``, or ``DorkEngine.ALL``
                     (combines results from all three, deduplicated).

    Returns:
        List of discovered URLs, or an empty list if all requests fail.
    """
    proxies = {"http": proxy, "https": proxy} if proxy else {}

    engines = (
        [DorkEngine.DDG, DorkEngine.BING, DorkEngine.YAHOO]
        if engine == DorkEngine.ALL
        else [engine]
    )

    seen: set[str] = set()
    found: list[str] = []

    for eng in engines:
        if eng == DorkEngine.DDG:
            urls = _dork_ddg(query, max_results, proxies, timeout)
        elif eng == DorkEngine.BING:
            urls = _dork_bing(query, max_results, proxies, timeout)
        elif eng == DorkEngine.YAHOO:
            urls = _dork_yahoo(query, max_results, proxies, timeout)
        else:
            continue

        for url in urls:
            if url not in seen:
                seen.add(url)
                found.append(url)
                if len(found) >= max_results * len(engines):
                    break

    return found[:max_results] if engine != DorkEngine.ALL else found


# ---------------------------------------------------------------------------
# Engine implementations
# ---------------------------------------------------------------------------

def _has_params(url: str) -> bool:
    try:
        return bool(urlparse(url).query)
    except requests.RequestException:
        return False


def _dork_ddg(query: str, max_results: int, proxies: dict, timeout: int) -> list[str]:
    try:
        resp = requests.get(
            _DDG_URL,
            params={"q": query},
            headers={"User-Agent": _DEFAULT_UA},
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []

    found: list[str] = []
    seen:  set[str]  = set()
    for m in _DDG_RE.finditer(resp.text):
        url    = unquote(m.group(1))
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.query and url not in seen:
            seen.add(url)
            found.append(url)
            if len(found) >= max_results:
                break
    return found


def _dork_bing(query: str, max_results: int, proxies: dict, timeout: int) -> list[str]:
    try:
        resp = requests.get(
            _BING_URL,
            params={"q": query, "count": min(max_results, 50)},
            headers={
                "User-Agent": _DEFAULT_UA,
                "Accept-Language": "en-US,en;q=0.9",
            },
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []

    found: list[str] = []
    seen:  set[str]  = set()

    # Primary: anchor hrefs with "tilk" class (Bing result links)
    for m in _BING_RE.finditer(resp.text):
        url = unquote(m.group(1))
        if _has_params(url) and url not in seen:
            seen.add(url)
            found.append(url)

    # Fallback: cite tags (plain URL display)
    if not found:
        for m in _BING_RE2.finditer(resp.text):
            url = unquote(m.group(1).strip())
            if url.startswith("http") and _has_params(url) and url not in seen:
                seen.add(url)
                found.append(url)

    return found[:max_results]


def _dork_yahoo(query: str, max_results: int, proxies: dict, timeout: int) -> list[str]:
    try:
        resp = requests.get(
            _YAHOO_URL,
            params={"p": query, "n": min(max_results, 100)},
            headers={
                "User-Agent": _DEFAULT_UA,
                "Accept-Language": "en-US,en;q=0.9",
            },
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return []

    found: list[str] = []
    seen:  set[str]  = set()

    # Yahoo wraps result URLs in /RU=<encoded-url>/RK= pattern
    for m in _YAHOO_RU.finditer(resp.text):
        url = unquote(m.group(1))
        if _has_params(url) and url not in seen:
            seen.add(url)
            found.append(url)

    # Fallback: direct anchor hrefs in result blocks
    if not found:
        for m in _YAHOO_RE.finditer(resp.text):
            url = unquote(m.group(1))
            if _has_params(url) and url not in seen:
                seen.add(url)
                found.append(url)

    return found[:max_results]
