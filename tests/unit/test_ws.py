# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Unit tests for commonhuman_core.ws — WebSocket injection helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from commonhuman_core.ws import (
    WEBSOCKET_AVAILABLE,
    WsResult,
    discover_ws_urls,
    ws_inject,
    _resolve_ws,
)


# ---------------------------------------------------------------------------
# WsResult dataclass
# ---------------------------------------------------------------------------

class TestWsResult:
    def test_default_values(self):
        r = WsResult(url="wss://example.com/ws", payload="hello")
        assert r.responses == []
        assert r.reflected is False
        assert r.marker == ""
        assert r.error is None

    def test_explicit_values(self):
        r = WsResult(url="wss://x.com", payload="p", reflected=True, marker="M", error="err")
        assert r.reflected is True
        assert r.marker == "M"
        assert r.error == "err"


# ---------------------------------------------------------------------------
# _resolve_ws
# ---------------------------------------------------------------------------

class TestResolveWs:
    def test_absolute_wss_returned_as_is(self):
        assert _resolve_ws("wss://example.com/ws", "") == "wss://example.com/ws"

    def test_absolute_ws_returned_as_is(self):
        assert _resolve_ws("ws://example.com/ws", "") == "ws://example.com/ws"

    def test_relative_path_with_https_base(self):
        result = _resolve_ws("/ws/chat", "https://example.com")
        assert result == "wss://example.com/ws/chat"

    def test_relative_path_with_http_base(self):
        result = _resolve_ws("/ws/chat", "http://example.com")
        assert result == "ws://example.com/ws/chat"

    def test_relative_path_without_base_returns_empty(self):
        assert _resolve_ws("/ws/chat", "") == ""

    def test_non_ws_non_slash_returns_empty(self):
        assert _resolve_ws("some-text", "") == ""

    def test_urlparse_exception_returns_empty(self):
        with patch("urllib.parse.urlparse", side_effect=ValueError("bad")):
            result = _resolve_ws("/ws/chat", "https://example.com")
        assert result == ""


# ---------------------------------------------------------------------------
# discover_ws_urls
# ---------------------------------------------------------------------------

class TestDiscoverWsUrls:
    def test_finds_new_websocket_call(self):
        html = 'var ws = new WebSocket("wss://example.com/ws/feed");'
        urls = discover_ws_urls(html, "https://example.com")
        assert "wss://example.com/ws/feed" in urls

    def test_finds_bare_wss_literal(self):
        html = 'var endpoint = "wss://chat.example.com/socket";'
        urls = discover_ws_urls(html, "")
        assert "wss://chat.example.com/socket" in urls

    def test_finds_relative_websocket_path(self):
        html = 'var ws = new WebSocket("/ws/notifications");'
        urls = discover_ws_urls(html, "https://app.example.com")
        assert "wss://app.example.com/ws/notifications" in urls

    def test_deduplicates_results(self):
        html = (
            'new WebSocket("wss://example.com/ws");\n'
            'new WebSocket("wss://example.com/ws");'
        )
        urls = discover_ws_urls(html, "")
        assert urls.count("wss://example.com/ws") == 1

    def test_empty_html_returns_empty(self):
        assert discover_ws_urls("", "") == []

    def test_no_ws_patterns_returns_empty(self):
        html = '<html><body><p>No websockets here</p></body></html>'
        assert discover_ws_urls(html, "") == []

    def test_multiple_different_endpoints(self):
        html = (
            'new WebSocket("wss://a.example.com/ws1");\n'
            'var x = "wss://b.example.com/ws2";'
        )
        urls = discover_ws_urls(html, "")
        assert len(urls) == 2

    def test_returns_list(self):
        assert isinstance(discover_ws_urls("", ""), list)


# ---------------------------------------------------------------------------
# ws_inject — tested with websocket-client mocked
# ---------------------------------------------------------------------------

class TestWsInject:
    def test_returns_empty_when_not_available(self, monkeypatch):
        import commonhuman_core.ws as ws_mod
        monkeypatch.setattr(ws_mod, "WEBSOCKET_AVAILABLE", False)
        result = ws_inject("wss://example.com/ws", ["payload"])
        assert result == []

    @pytest.mark.skipif(not WEBSOCKET_AVAILABLE, reason="websocket-client not installed")
    def test_returns_one_result_per_payload(self):
        import websocket as _ws_mod
        mock_ws = MagicMock()
        mock_ws.recv.side_effect = _ws_mod.WebSocketTimeoutException

        with patch("commonhuman_core.ws.websocket.create_connection", return_value=mock_ws):
            results = ws_inject("wss://example.com/ws", ["payload1", "payload2"], timeout=1)

        assert len(results) == 2
        assert all(isinstance(r, WsResult) for r in results)

    @pytest.mark.skipif(not WEBSOCKET_AVAILABLE, reason="websocket-client not installed")
    def test_detects_marker_reflection(self):
        marker = "StingXSS_test"
        mock_ws = MagicMock()
        # First recv returns the marker, then times out
        import websocket as _ws_mod
        mock_ws.recv.side_effect = [f'<img src=x onerror="{marker}">', _ws_mod.WebSocketTimeoutException]

        with patch("commonhuman_core.ws.websocket.create_connection", return_value=mock_ws):
            results = ws_inject(
                "wss://example.com/ws",
                [f'<img src=x onerror="{marker}">'],
                marker=marker,
                timeout=1,
            )

        assert results[0].reflected is True
        assert marker in results[0].responses[0]

    @pytest.mark.skipif(not WEBSOCKET_AVAILABLE, reason="websocket-client not installed")
    def test_records_error_on_connection_failure(self):
        import websocket as _ws_mod
        with patch(
            "commonhuman_core.ws.websocket.create_connection",
            side_effect=_ws_mod.WebSocketException("refused"),
        ):
            results = ws_inject("wss://example.com/ws", ["p"], timeout=1)

        assert results[0].error == "refused"
        assert results[0].reflected is False

    @pytest.mark.skipif(not WEBSOCKET_AVAILABLE, reason="websocket-client not installed")
    def test_records_error_on_oserror(self):
        with patch(
            "commonhuman_core.ws.websocket.create_connection",
            side_effect=OSError("connection refused"),
        ):
            results = ws_inject("wss://example.com/ws", ["p"], timeout=1)

        assert results[0].error == "connection refused"

    @pytest.mark.skipif(not WEBSOCKET_AVAILABLE, reason="websocket-client not installed")
    def test_loop_exhausts_max_recv_naturally(self):
        import websocket as _ws_mod
        mock_ws = MagicMock()
        mock_ws.recv.side_effect = ["frame1", "frame2", "frame3"]

        with patch("commonhuman_core.ws.websocket.create_connection", return_value=mock_ws):
            results = ws_inject("wss://example.com/ws", ["p"], timeout=1, max_recv=3)

        assert len(results[0].responses) == 3

    @pytest.mark.skipif(not WEBSOCKET_AVAILABLE, reason="websocket-client not installed")
    def test_empty_frame_skipped(self):
        import websocket as _ws_mod
        mock_ws = MagicMock()
        mock_ws.recv.side_effect = ["", "real_frame", _ws_mod.WebSocketTimeoutException]

        with patch("commonhuman_core.ws.websocket.create_connection", return_value=mock_ws):
            results = ws_inject("wss://example.com/ws", ["p"], timeout=1)

        assert results[0].responses == ["real_frame"]

    @pytest.mark.skipif(not WEBSOCKET_AVAILABLE, reason="websocket-client not installed")
    def test_cookie_passed_in_header(self):
        import websocket as _ws_mod
        mock_ws = MagicMock()
        mock_ws.recv.side_effect = _ws_mod.WebSocketTimeoutException
        captured_headers = []

        def fake_create(url, timeout, header, suppress_origin):
            captured_headers.extend(header)
            return mock_ws

        with patch("commonhuman_core.ws.websocket.create_connection", side_effect=fake_create):
            ws_inject("wss://example.com/ws", ["p"], cookies="session=abc", timeout=1)

        cookie_header = next((h for h in captured_headers if h.startswith("Cookie:")), None)
        assert cookie_header == "Cookie: session=abc"
