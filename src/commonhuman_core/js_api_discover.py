# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""
commonhuman-core — js_api_discover.py

Extract REST / JSON API endpoints from SPA JavaScript bundles.

Approach:
  1. Fetch the seed page and collect all <script src> bundle URLs.
  2. Download each bundle and apply two passes:
     a. HTTP-method calls with template literals:
        get(`${base}/rest/products/search?q=${e}`)
        → GET /rest/products/search?q=1
     b. Hardcoded path strings used as URL fragments:
        "/rest/track-order", "/api/Products"
        → GET /rest/track-order/1, GET /api/Products
  3. Resolve template variables to benign placeholders.
  4. Return deduplicated (method, full_url, path_template) tuples.
"""

from __future__ import annotations

import re
import urllib.parse as up
from typing import List, Optional, Tuple

import requests

_BUNDLE_SCRIPT_RE  = re.compile(r'<script[^>]+src="([^"]*\.js)"', re.IGNORECASE)
_JS_CHUNK_RE       = re.compile(r'\b(chunk-[A-Za-z0-9_\-]+\.js)\b')
_METHOD_TMPL_RE    = re.compile(
    r'\b(get|post|put|patch|delete)\s*\(\s*`([^`]*/(rest|api)/[^`]*)`',
    re.IGNORECASE,
)
_STATIC_PATH_RE    = re.compile(
    r'["\'](\/(rest|api)\/[A-Za-z0-9/_\-?=&%]+)["\']',
)
_TMPL_VAR_RE       = re.compile(r'\$\{[^}]+\}')
# Indirect concatenation: this.host + "/suffix" where host contains /rest/...
# e.g.  host=this.hostServer+"/rest/user"  →  this.http.post(this.host+"/login",...)
_VAR_ASSIGN_RE     = re.compile(
    r'(?:this\.)?(\w+)\s*=\s*[^;]+?["\'](/(?:rest|api)/[A-Za-z0-9/_\-]+)["\']',
)
_VAR_SUFFIX_RE     = re.compile(
    r'\b(get|post|put|patch|delete)\s*\(\s*(?:this\.)?(\w+)\s*\+\s*["\']([/A-Za-z0-9_\-]+)["\']',
    re.IGNORECASE,
)
_TIMEOUT           = 10
_MAX_BUNDLE_BYTES  = 10 * 1024 * 1024   # 10 MB per bundle


def js_api_discover(
    base_url:   str,
    session:    Optional[requests.Session] = None,
    max_bundles: int = 20,
) -> List[Tuple[str, str, str]]:
    """
    Discover API endpoints by parsing SPA JavaScript bundles at *base_url*.

    Returns a list of ``(method, full_url, raw_template)`` tuples.
    *method* is upper-case (``"GET"``, ``"POST"`` …).
    *full_url* has template variables replaced with probe placeholders.
    *raw_template* is the original pattern as found in the JS.
    """
    sess = session or requests.Session()
    parsed = up.urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    bundle_urls = _collect_bundle_urls(base_url, origin, sess, max_bundles)
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for bundle_url in bundle_urls:
        try:
            resp = sess.get(bundle_url, timeout=_TIMEOUT)
            if not resp.ok:
                continue
            js = resp.text[:_MAX_BUNDLE_BYTES]
        except OSError:
            continue

        for method, url, template in _extract_method_templates(js, origin):
            key = f"{method}:{url}"
            if key not in seen:
                seen.add(key)
                results.append((method, url, template))

        for method, url, template in _extract_static_paths(js, origin):
            key = f"{method}:{url}"
            if key not in seen:
                seen.add(key)
                results.append((method, url, template))

        for method, url, template in _extract_indirect_concat(js, origin):
            key = f"{method}:{url}"
            if key not in seen:
                seen.add(key)
                results.append((method, url, template))

    return results


def _collect_bundle_urls(
    base_url: str, origin: str, sess: requests.Session, max_n: int
) -> list[str]:
    try:
        resp = sess.get(base_url, timeout=_TIMEOUT)
        html = resp.text
    except OSError:
        return []

    urls: list[str] = []
    seen: set[str] = set()

    def _add(src: str) -> bool:
        if not src.startswith("http"):
            src = origin.rstrip("/") + "/" + src.lstrip("/")
        if src not in seen and len(urls) < max_n:
            seen.add(src)
            urls.append(src)
            return True
        return False

    for m in _BUNDLE_SCRIPT_RE.finditer(html):
        _add(m.group(1))

    # Also scan chunk files referenced inside the primary bundles
    for bundle_url in list(urls):
        try:
            js = sess.get(bundle_url, timeout=_TIMEOUT).text[:_MAX_BUNDLE_BYTES]
        except OSError:
            continue
        for m in _JS_CHUNK_RE.finditer(js):
            _add(m.group(1))
            if len(urls) >= max_n:
                return urls

    return urls


_HOST_VAR_RE = re.compile(
    r'\$\{[^}]*(host|server|url|base|origin)[^}]*\}', re.IGNORECASE
)
_ID_VAR_RE = re.compile(
    r'\$\{[^}]*(id|Id|ID|num|page|limit|offset|count)[^}]*\}'
)


def _resolve_template(template: str, origin: str) -> str:
    """Replace ${...} placeholders with benign probe values."""
    template = _HOST_VAR_RE.sub(origin, template)
    template = _ID_VAR_RE.sub('1', template)
    # Replace query-value variables (keep param name from context if possible)
    template = _TMPL_VAR_RE.sub('test', template)
    return template


def _extract_indirect_concat(js: str, origin: str) -> list[tuple[str, str, str]]:
    """
    Handle indirect path construction like:
      host = this.hostServer + "/rest/user"
      this.http.post(this.host + "/login", body)
    → POST /rest/user/login
    """
    # Build map: variable name → base path
    var_bases: dict[str, str] = {}
    for m in _VAR_ASSIGN_RE.finditer(js):
        var_name  = m.group(1)
        base_path = m.group(2)
        if base_path.startswith(("/rest/", "/api/")):
            var_bases[var_name] = base_path

    results = []
    for m in _VAR_SUFFIX_RE.finditer(js):
        method   = m.group(1).upper()
        var_name = m.group(2)
        suffix   = m.group(3)
        base     = var_bases.get(var_name)
        if base is None:
            continue
        path     = base.rstrip("/") + "/" + suffix.lstrip("/")
        full_url = origin.rstrip("/") + path
        results.append((method, full_url, f"{var_name} + {suffix!r}"))
    return results


def _extract_method_templates(js: str, origin: str) -> list[tuple[str, str, str]]:
    results = []
    for m in _METHOD_TMPL_RE.finditer(js):
        method   = m.group(1).upper()
        template = m.group(2)
        resolved = _resolve_template(template, origin)
        # Ensure it's a full URL
        if not resolved.startswith("http"):
            resolved = origin.rstrip("/") + "/" + resolved.lstrip("/")
        results.append((method, resolved, template))
    return results


def _extract_static_paths(js: str, origin: str) -> list[tuple[str, str, str]]:
    """
    Extract hardcoded path strings like "/rest/track-order" and infer method
    from the nearest preceding HTTP-method keyword in the same expression.
    """
    results = []
    for m in _STATIC_PATH_RE.finditer(js):
        path     = m.group(1)

        # Look back up to 60 chars for an HTTP method hint
        start   = max(0, m.start() - 60)
        context = js[start:m.start()]
        method  = "GET"
        for verb in ("delete", "patch", "put", "post", "get"):
            if re.search(r'\b' + verb + r'\s*\(', context, re.IGNORECASE):
                method = verb.upper()
                break

        full_path = path
        full_url  = origin.rstrip("/") + full_path
        results.append((method, full_url, path))

        # Also emit a path-param probe variant for endpoints that look like
        # they accept a trailing path segment (no query string, ends in a word,
        # and is used as a base in the JS — e.g. /rest/track-order → /rest/track-order/1)
        if '?' not in path and not path.endswith('/'):
            last_seg = path.rstrip('/').rsplit('/', 1)[-1]
            if last_seg and last_seg.replace('-', '').isalpha():
                probe_url = full_url.rstrip('/') + '/1'
                results.append((method, probe_url, path + '/{param}'))
    return results
