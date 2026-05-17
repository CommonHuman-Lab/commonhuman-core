# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""DuckDuckGo-based URL discovery (dorking).

Fetches DuckDuckGo HTML results and returns URLs that carry query parameters —
the candidates most likely to have injectable surfaces.  No API key required.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

import requests

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_UDDG_RE = re.compile(r'uddg=([^&"\s]+)')
_DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def dork(
    query: str,
    max_results: int = 20,
    proxy: str = "",
    timeout: int = 15,
) -> list[str]:
    """Query DuckDuckGo and return URLs that contain query parameters.

    Results are returned deduplicated, in result-rank order.  Only ``http``
    and ``https`` URLs with at least one query parameter are included — these
    are the most likely candidates for parameter-injection testing.

    Args:
        query:       DuckDuckGo search query (e.g. ``'site:example.com inurl:search'``).
        max_results: Maximum number of URLs to return (default 20).
        proxy:       Optional HTTP proxy URL (e.g. ``'http://127.0.0.1:8080'``).
        timeout:     Request timeout in seconds (default 15).

    Returns:
        List of discovered URLs, or an empty list if the request fails.
    """
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    try:
        resp = requests.get(
            _DDG_HTML_URL,
            params={"q": query},
            headers={"User-Agent": _DEFAULT_UA},
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:
        return []

    found: list[str] = []
    seen: set[str] = set()
    for m in _UDDG_RE.finditer(resp.text):
        url = unquote(m.group(1))
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.query and url not in seen:
            seen.add(url)
            found.append(url)
            if len(found) >= max_results:
                break
    return found
