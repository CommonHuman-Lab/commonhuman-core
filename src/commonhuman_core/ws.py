# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""WebSocket client for injection testing.

Provides a simple synchronous interface for connecting to a WebSocket endpoint,
sending payloads, and collecting any response frames — sufficient for XSS /
injection testing without requiring full async infrastructure.

Requires the ``websocket-client`` package (optional dependency)::

    pip install 'commonhuman-core[websocket]'

Usage::

    from commonhuman_core.ws import ws_inject, WsResult, WEBSOCKET_AVAILABLE

    if WEBSOCKET_AVAILABLE:
        result = ws_inject(
            url="wss://example.com/ws",
            payloads=['{"msg": "<img src=x onerror=alert(1)>"}'],
            cookies="session=abc",
            timeout=10,
        )
        for frame in result.responses:
            if "onerror" in frame:
                print("Reflected in WS response!")
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["WEBSOCKET_AVAILABLE", "WsResult", "ws_inject", "discover_ws_urls"]

# ---------------------------------------------------------------------------
# Optional dependency guard
# ---------------------------------------------------------------------------

try:
    import websocket  # type: ignore[import-untyped]
    WEBSOCKET_AVAILABLE = True
except ImportError:  # pragma: no cover
    WEBSOCKET_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class WsResult:
    """Result of injecting one or more payloads into a WebSocket endpoint."""
    url:          str
    payload:      str
    responses:    List[str]          = field(default_factory=list)
    reflected:    bool               = False  # any response contained the marker
    marker:       str                = ""
    error:        Optional[str]      = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ws_inject(
    url:      str,
    payloads: List[str],
    cookies:  str = "",
    headers:  Optional[Dict[str, str]] = None,
    timeout:  int = 10,
    marker:   str = "",
    max_recv: int = 5,
) -> List[WsResult]:
    """Send each payload over a WebSocket connection and collect responses.

    Opens a fresh connection per payload to avoid state leakage between
    injections.  Each response frame is checked for *marker* reflection.

    Args:
        url:      WebSocket URL (``ws://`` or ``wss://``).
        payloads: List of strings to send as individual messages.
        cookies:  Cookie header value (``name=val; name2=val2``).
        headers:  Additional HTTP handshake headers.
        timeout:  Connection + receive timeout in seconds (default 10).
        marker:   String to search for in responses (e.g. a StingXSS marker).
        max_recv: Maximum response frames to collect per payload (default 5).

    Returns:
        List of ``WsResult``, one per payload.  Empty list if
        ``WEBSOCKET_AVAILABLE`` is False.
    """
    if not WEBSOCKET_AVAILABLE:
        logger.warning("ws_inject: websocket-client not installed — skipping")
        return []

    results: List[WsResult] = []
    for payload in payloads:
        result = _inject_one(url, payload, cookies, headers or {}, timeout, marker, max_recv)
        results.append(result)
    return results


def discover_ws_urls(html: str, base_url: str = "") -> List[str]:
    """Extract WebSocket URLs from HTML / JavaScript source.

    Searches for ``new WebSocket(...)`` calls and ``ws://`` / ``wss://``
    string literals.

    Args:
        html:     HTML or JavaScript source text.
        base_url: Used to resolve ``/path`` style relative WS URLs.

    Returns:
        Deduplicated list of WebSocket URLs found in the source.
    """
    patterns = [
        # new WebSocket("wss://...")
        re.compile(r"""new\s+WebSocket\s*\(\s*["'`](wss?://[^"'`\s)]+)["'`]""", re.IGNORECASE),
        # bare wss:// or ws:// string literals
        re.compile(r"""["'`](wss?://[^"'`\s)]{4,})["'`]""", re.IGNORECASE),
        # relative paths used with WebSocket: new WebSocket("/ws/chat")
        re.compile(r"""new\s+WebSocket\s*\(\s*["'`](/[^"'`\s)]+)["'`]""", re.IGNORECASE),
    ]

    found:  List[str] = []
    seen:   set[str]  = set()

    for pat in patterns:
        for m in pat.finditer(html):
            raw = m.group(1)
            url = _resolve_ws(raw, base_url)
            if url and url not in seen:
                seen.add(url)
                found.append(url)

    return found


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _inject_one(
    url:      str,
    payload:  str,
    cookies:  str,
    headers:  Dict[str, str],
    timeout:  int,
    marker:   str,
    max_recv: int,
) -> WsResult:
    result = WsResult(url=url, payload=payload, marker=marker)
    extra_headers = dict(headers)
    if cookies:
        extra_headers["Cookie"] = cookies

    try:
        ws = websocket.create_connection(
            url,
            timeout=timeout,
            header=[f"{k}: {v}" for k, v in extra_headers.items()],
            suppress_origin=True,
        )
        try:
            ws.send(payload)
            for _ in range(max_recv):
                ws.settimeout(timeout)
                try:
                    frame = ws.recv()
                    if frame:
                        result.responses.append(str(frame))
                        if marker and marker in str(frame):
                            result.reflected = True
                except websocket.WebSocketTimeoutException:
                    break
        finally:
            ws.close()
    except websocket.WebSocketException as exc:
        result.error = str(exc)
    except OSError as exc:
        result.error = str(exc)

    return result


def _resolve_ws(raw: str, base_url: str) -> str:
    """Turn a raw WebSocket reference into an absolute ws:// or wss:// URL."""
    if raw.startswith(("ws://", "wss://")):
        return raw
    if base_url and raw.startswith("/"):
        # Convert http(s):// base to ws(s)://
        scheme = "wss" if base_url.startswith("https") else "ws"
        try:
            from urllib.parse import urlparse
            p = urlparse(base_url)
            return f"{scheme}://{p.netloc}{raw}"
        except Exception:
            pass
    return ""
