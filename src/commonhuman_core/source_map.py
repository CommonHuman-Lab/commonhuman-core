# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""JavaScript source map fetching and original-source extraction.

Bundled/minified JavaScript files embed a ``//# sourceMappingURL=`` comment
that points to a ``.map`` file.  That file contains the original (pre-minification)
source code, which is far more useful for DOM XSS analysis than the minified bundle.

This module fetches source maps and returns the original source files, keyed by
their original path, so the DOM scanner can analyse real code rather than
machine-generated minified output.

Usage::

    from commonhuman_core.source_map import fetch_source_maps

    originals = fetch_source_maps(
        js_urls=["https://example.com/static/bundle.js"],
        fetcher=lambda url: session.get(url).text,
        base_url="https://example.com",
    )
    # originals: dict[original_path -> source_text]
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse as up
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["fetch_source_maps", "SourceMapResult"]

# Matches: //# sourceMappingURL=foo.js.map
_MAP_URL_RE = re.compile(
    r"//[#@]\s*sourceMappingURL=([^\s\"']+)",
    re.IGNORECASE,
)

# Matches data: URI source maps (inline, base64-encoded)
_DATA_URI_RE = re.compile(
    r"data:application/json[^;]*;base64,([A-Za-z0-9+/=]+)",
    re.IGNORECASE,
)


class SourceMapResult:
    """Holds all original sources recovered from source maps for one or more JS files."""

    def __init__(self) -> None:
        # original_path → source_text
        self.sources: Dict[str, str] = {}
        # js_url → list of original source paths it mapped to
        self.mapping: Dict[str, List[str]] = {}

    def __len__(self) -> int:
        return len(self.sources)

    def all_sources(self) -> List[str]:
        """All original source texts (values), deduplicated."""
        return list(self.sources.values())


def fetch_source_maps(
    js_urls:  List[str],
    fetcher:  Callable[[str], str],
    base_url: str = "",
    max_maps: int = 30,
) -> SourceMapResult:
    """Fetch source maps for *js_urls* and return recovered original sources.

    For each JS URL:
    1. Fetches the JS file (via *fetcher*).
    2. Looks for ``//# sourceMappingURL=`` comment.
    3. Fetches the ``.map`` file (or decodes inline base64).
    4. Extracts original source code from the ``sourcesContent`` array.

    Args:
        js_urls:  List of JavaScript bundle URLs to probe.
        fetcher:  Callable ``(url: str) -> str`` for fetching text content.
                  Typically ``lambda url: injector.get(url).text``.
        base_url: Base URL for resolving relative source map paths.
        max_maps: Maximum number of source maps to fetch (default 30).

    Returns:
        ``SourceMapResult`` with ``sources`` dict (path → original source).
    """
    result = SourceMapResult()
    maps_fetched = 0

    for js_url in js_urls:
        if maps_fetched >= max_maps:
            break
        try:
            js_src = fetcher(js_url)
        except Exception as exc:
            logger.debug("source_map: could not fetch %s: %s", js_url, exc)
            continue

        map_url = _find_map_url(js_src, js_url, base_url)
        if not map_url:
            continue

        map_data = _fetch_map(map_url, js_src, fetcher)
        if not map_data:
            continue

        maps_fetched += 1
        paths = _extract_sources(js_url, map_url, map_data, result)
        result.mapping[js_url] = paths
        logger.info("source_map: recovered %d original source(s) from %s", len(paths), js_url)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_map_url(js_src: str, js_url: str, base_url: str) -> Optional[str]:
    """Locate the sourceMappingURL in *js_src* and return an absolute URL."""
    m = _MAP_URL_RE.search(js_src[-4096:])  # comment is always near end of file
    if not m:
        return None

    ref = m.group(1).strip()

    # Inline data URI — signal to caller that the map is embedded in the JS
    if ref.startswith("data:"):
        return ref

    # Absolute URL
    if ref.startswith(("http://", "https://")):
        return ref

    # Relative to the JS file's location
    return up.urljoin(js_url or base_url, ref)


def _fetch_map(map_url: str, js_src: str, fetcher: Callable[[str], str]) -> Optional[dict]:
    """Fetch and parse the source map JSON.  Handles inline data URIs."""
    if map_url.startswith("data:"):
        m = _DATA_URI_RE.search(map_url)
        if not m:
            return None
        try:
            import base64
            raw = base64.b64decode(m.group(1)).decode("utf-8", errors="replace")
            return json.loads(raw)
        except Exception as exc:
            logger.debug("source_map: failed to decode inline map: %s", exc)
            return None

    try:
        raw = fetcher(map_url)
        if not raw:
            return None
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.debug("source_map: JSON parse error in %s: %s", map_url, exc)
        return None
    except Exception as exc:
        logger.debug("source_map: could not fetch map %s: %s", map_url, exc)
        return None


def _extract_sources(
    js_url:   str,
    map_url:  str,
    map_data: dict,
    result:   SourceMapResult,
) -> List[str]:
    """Extract original sources from a parsed source map dict.

    Populates *result.sources* and returns the list of original path keys added.
    """
    sources:         List[str] = map_data.get("sources") or []
    sources_content: List[str] = map_data.get("sourcesContent") or []
    source_root:     str       = (map_data.get("sourceRoot") or "").rstrip("/")

    paths_added: List[str] = []

    for i, src_path in enumerate(sources):
        # Skip node_modules and test files — not interesting for DOM XSS
        if _is_noise(src_path):
            continue

        # Build a canonical key for this source
        if source_root and not src_path.startswith(("http", "/")):
            canonical = f"{source_root}/{src_path}"
        else:
            canonical = src_path

        # Get the source text (may be None / missing)
        src_text = ""
        if i < len(sources_content) and sources_content[i]:
            src_text = sources_content[i]

        if src_text and canonical not in result.sources:
            result.sources[canonical] = src_text
            paths_added.append(canonical)

    return paths_added


_NOISE_PATTERNS = re.compile(
    r"node_modules/|webpack/runtime|__webpack_require__|"
    r"\.spec\.[jt]s$|\.test\.[jt]s$|/vendor/",
    re.IGNORECASE,
)


def _is_noise(path: str) -> bool:
    return bool(_NOISE_PATTERNS.search(path))
