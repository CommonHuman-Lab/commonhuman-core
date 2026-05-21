# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""
Multi-threaded BFS web crawler.

Discovers links and HTML forms within a target origin.
Respects same-origin constraint, max depth, max page limits, and
optional URL exclusion patterns.
"""

from __future__ import annotations

import re
import urllib.parse as up
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional, Set, Tuple

from .http.client import HttpClient

# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class FormTarget:
    """An HTML form discovered during crawling."""
    method:    str                        # "GET" | "POST"
    params:    Dict[str, str]             # {name: default_value} — injectable fields
    action:    str                        # resolved absolute action URL
    base_data: Dict[str, str] = field(default_factory=dict)  # hidden / submit fields


@dataclass
class CrawlResult:
    """Aggregated output of a crawl run."""
    visited_urls:        List[str]                   = field(default_factory=list)
    form_targets:        List[FormTarget]            = field(default_factory=list)
    url_params:          List[Tuple[str, List[str]]] = field(default_factory=list)
    page_sources:        Dict[str, str]              = field(default_factory=dict)
    path_param_candidates: List[str]                 = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def crawl(
    start_url:        str,
    injector:         HttpClient,
    max_pages:        int = 50,
    max_depth:        int = 3,
    threads:          int = 5,
    same_origin:      bool = True,
    exclude_patterns: Optional[List[str]] = None,
) -> CrawlResult:
    """BFS crawl from ``start_url``.

    Parameters
    ----------
    start_url:
        URL to begin crawling from.
    injector:
        An :class:`~commonhuman_core.http.HttpClient` (or subclass) used for
        all HTTP requests.
    max_pages:
        Stop after visiting this many unique pages.
    max_depth:
        Maximum BFS depth from ``start_url``.
    threads:
        Thread-pool size for parallel page fetching.
    same_origin:
        If ``True`` (default), skip URLs that are off-origin.
    exclude_patterns:
        Optional list of regex strings.  Any URL matching one is skipped.

    Returns
    -------
    CrawlResult
        Discovered pages, forms, URL parameters, and raw page sources.
    """
    compiled_excludes = [re.compile(p) for p in (exclude_patterns or [])]

    def _is_excluded(url: str) -> bool:
        return any(p.search(url) for p in compiled_excludes)

    result:  CrawlResult = CrawlResult()
    visited: Set[str]    = set()
    queue:   deque       = deque()
    queue.append((_normalise(start_url), 0))

    with ThreadPoolExecutor(max_workers=threads) as pool:
        while queue and len(visited) < max_pages:
            batch: List[Tuple[str, int]] = []
            while queue and len(batch) < threads * 2:
                url, depth = queue.popleft()
                norm = _normalise(url)
                if norm in visited:
                    continue
                if same_origin and not injector.same_origin(norm, start_url):
                    continue
                if _is_excluded(norm):
                    continue
                visited.add(norm)
                batch.append((norm, depth))

            if not batch:
                break

            futures = {
                pool.submit(_fetch_page, url, injector): (url, depth)
                for url, depth in batch
            }

            for future in as_completed(futures):
                url, depth = futures[future]
                try:
                    page = future.result()
                except Exception:
                    continue

                if page is None:
                    continue
                html, links, forms = page

                params = injector.get_params(url)
                if params:
                    result.url_params.append((url, params))

                if not html:
                    # If the URL has a numeric path segment, surface it so callers
                    # can probe path-parameter injection (e.g. /item/1).
                    _path_parts = up.urlparse(url).path.split("/")
                    if any(p and p.lstrip("-").isdigit() for p in _path_parts):
                        result.path_param_candidates.append(url)
                    continue

                result.visited_urls.append(url)
                result.page_sources[url] = html

                for form in forms:
                    result.form_targets.append(form)
                    # Enqueue the form action URL for a GET visit so we discover
                    # what's at that endpoint (even for POST forms like subscribe pages).
                    if depth < max_depth:
                        action_norm = _normalise(form.action)
                        if action_norm not in visited and not _is_excluded(action_norm):
                            if not same_origin or injector.same_origin(action_norm, start_url):
                                queue.append((action_norm, depth + 1))

                if depth < max_depth:
                    for link in links:
                        norm = _normalise(link)
                        if norm not in visited and not _is_excluded(norm):
                            queue.append((norm, depth + 1))

    return result


# ---------------------------------------------------------------------------
# Page fetching
# ---------------------------------------------------------------------------


def _fetch_page(
    url: str,
    injector: HttpClient,
) -> Optional[Tuple[str, List[str], List[FormTarget]]]:
    try:
        resp = injector.get(url)
    except Exception:
        return None

    if resp.status_code >= 400:
        return None

    ct = resp.headers.get("content-type", "")
    if "html" not in ct and "javascript" not in ct:
        return ("", [], [])

    html = resp.text
    # Use the final URL after redirects as the base so relative links and
    # form actions resolve correctly (critical for 301 /path → /path/ redirects).
    resp_url = getattr(resp, "url", None)
    effective_url = resp_url if isinstance(resp_url, str) and resp_url else url
    return html, _extract_links(html, effective_url), _extract_forms(html, effective_url)


# ---------------------------------------------------------------------------
# HTML parsers
# ---------------------------------------------------------------------------


_CODE_PATH_RE = re.compile(
    r'^(/(?:[A-Za-z0-9_\-]+/){2,}(?:[A-Za-z0-9_\-]+|\{[^}]+\}|:[A-Za-z][A-Za-z0-9_]*)(?:\?[^\s"\'<>]*)?)'
)


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url    = base_url
        self.links:      List[str] = []
        self._in_code    = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        attr_dict = {k.lower(): v for k, v in attrs if v is not None}

        if tag_lower == "code":
            self._in_code = True
            return

        # Standard anchor links
        if tag_lower == "a":
            href = attr_dict.get("href", "").strip()
            if href and not href.startswith(("javascript:", "mailto:", "#")):
                self._add(href)

        # <button formaction="..."> — submits to a different URL than its parent form
        if tag_lower == "button":
            fa = attr_dict.get("formaction", "").strip()
            if fa and not fa.startswith(("javascript:", "mailto:", "#")):
                self._add(fa)

        # data-href / data-url / data-link / data-action on any element
        for data_attr in ("data-href", "data-url", "data-link", "data-action"):
            val = attr_dict.get(data_attr, "").strip()
            if val and not val.startswith(("javascript:", "mailto:", "#", "{")):
                self._add(val)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "code":
            self._in_code = False

    def handle_data(self, data: str) -> None:
        if not self._in_code:
            return
        text = data.strip()
        # Extract the leading path from <code> content (e.g. "/api/items/1?q=x").
        path_only = text.split("?")[0].split(" ")[0]
        if _CODE_PATH_RE.match(path_only):
            self._add(text)

    def _add(self, href: str) -> None:
        try:
            abs_url = up.urljoin(self.base_url, href)
            parsed  = up.urlparse(abs_url)
            self.links.append(up.urlunparse(parsed._replace(fragment="")))
        except Exception:  # pragma: no cover
            pass


class _FormParser(HTMLParser):
    _SKIP_TYPES   = {"button", "image", "reset"}
    _SUBMIT_TYPES = {"submit"}
    _HIDDEN_TYPES = {"hidden"}

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url        = base_url
        self.forms: List[FormTarget] = []
        self._in_form        = False
        self._current_action = base_url
        self._current_method = "GET"
        self._current_params: Dict[str, str] = {}
        self._current_base:   Dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag      = tag.lower()
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag == "form":
            self._in_form = True
            action_raw = attr_dict.get("action", "").strip()
            try:
                self._current_action = (
                    up.urljoin(self.base_url, action_raw) if action_raw else self.base_url
                )
            except Exception:  # pragma: no cover
                self._current_action = self.base_url
            self._current_method = attr_dict.get("method", "GET").upper()
            self._current_params = {}
            self._current_base   = {}

        elif self._in_form and tag == "input":
            input_type = attr_dict.get("type", "text").lower()
            name = attr_dict.get("name", "").strip()
            if not name or input_type in self._SKIP_TYPES:
                return
            if input_type in self._SUBMIT_TYPES:
                self._current_base[name] = attr_dict.get("value", "")
            elif input_type in self._HIDDEN_TYPES:
                self._current_base[name] = attr_dict.get("value", "")
            else:
                self._current_params[name] = attr_dict.get("value", "")

        elif self._in_form and tag in ("textarea", "select"):
            name = attr_dict.get("name", "").strip()
            if name:
                self._current_params[name] = ""

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._in_form:
            if self._current_params:
                self.forms.append(FormTarget(
                    method=self._current_method,
                    params=self._current_params,
                    action=self._current_action,
                    base_data=self._current_base,
                ))
            self._in_form        = False
            self._current_params = {}
            self._current_base   = {}


def _extract_links(html: str, base_url: str) -> List[str]:
    parser = _LinkParser(base_url)
    try:
        parser.feed(html)
    except Exception:  # pragma: no cover
        pass
    return parser.links


def _extract_forms(html: str, base_url: str) -> List[FormTarget]:
    parser = _FormParser(base_url)
    try:
        parser.feed(html)
    except Exception:  # pragma: no cover
        pass
    return parser.forms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise(url: str) -> str:
    """Lowercase scheme+host, strip trailing slash and fragment."""
    try:
        p = up.urlparse(url)
        return up.urlunparse((
            p.scheme.lower(),
            p.netloc.lower(),
            p.path.rstrip("/") or "/",
            p.params,
            p.query,
            "",
        ))
    except Exception:  # pragma: no cover
        return url
