# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Tests for commonhuman_core.http.HttpClient."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from commonhuman_core.http import HttpClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resp(status: int = 200, headers: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers     = headers or {}
    return r


def _client(**kwargs) -> HttpClient:
    kwargs.setdefault("timeout", 5)
    kwargs.setdefault("delay", 0.0)
    c = HttpClient(**kwargs)
    c._session = MagicMock()
    return c


# ---------------------------------------------------------------------------
# Session setup
# ---------------------------------------------------------------------------


class TestSessionSetup:
    def test_default_ua_is_set(self):
        c = HttpClient(timeout=5)
        ua = c._session.headers.get("User-Agent", "")
        assert "Mozilla" in ua

    def test_custom_headers_merged(self):
        c = HttpClient(timeout=5, headers={"X-Custom": "yes"})
        assert c._session.headers.get("X-Custom") == "yes"

    def test_proxy_configured(self):
        c = HttpClient(timeout=5, proxy="http://127.0.0.1:8080")
        assert c._session.proxies["http"] == "http://127.0.0.1:8080"
        assert c._session.proxies["https"] == "http://127.0.0.1:8080"

    def test_cookie_string_parsed(self):
        c = HttpClient(timeout=5, cookies="session=abc; token=xyz")
        assert c._session.cookies.get("session") == "abc"
        assert c._session.cookies.get("token") == "xyz"

    def test_verify_ssl_false_by_default(self):
        c = HttpClient(timeout=5)
        assert c._session.verify is False

    def test_verify_ssl_true(self):
        c = HttpClient(timeout=5, verify_ssl=True)
        assert c._session.verify is True

    def test_delay_clamped_to_zero(self):
        c = HttpClient(delay=-1.0)
        assert c.delay == 0.0

    def test_auth_set_on_session_when_provided(self):
        from requests.auth import HTTPBasicAuth
        auth = HTTPBasicAuth("user", "pass")
        c = HttpClient(timeout=5, auth=auth)
        assert c._session.auth is auth

    def test_auth_not_set_when_none(self):
        c = HttpClient(timeout=5)
        assert c._session.auth is None


# ---------------------------------------------------------------------------
# request_count
# ---------------------------------------------------------------------------


class TestRequestCount:
    def test_get_increments_count(self):
        c = _client()
        c._session.get.return_value = _resp()
        c.get("https://example.com/")
        assert c.request_count == 1

    def test_post_increments_count(self):
        c = _client()
        c._session.post.return_value = _resp()
        c.post("https://example.com/")
        assert c.request_count == 1

    def test_head_increments_count(self):
        c = _client()
        c._session.head.return_value = _resp()
        c.head("https://example.com/")
        assert c.request_count == 1

    def test_multiple_requests_accumulate(self):
        c = _client()
        c._session.get.return_value = _resp()
        c.get("https://example.com/")
        c.get("https://example.com/")
        assert c.request_count == 2


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_429_triggers_retry(self):
        c = _client()
        first  = _resp(429, {"Retry-After": "0"})
        second = _resp(200)
        c._session.get.side_effect = [first, second]
        with patch("commonhuman_core.http.client.time.sleep"):
            resp = c.get("https://example.com/")
        assert resp.status_code == 200

    def test_retry_after_header_respected(self):
        c = _client()
        first  = _resp(429, {"Retry-After": "7"})
        second = _resp(200)
        c._session.get.side_effect = [first, second]
        with patch("commonhuman_core.http.client.time.sleep") as mock_sleep:
            c.get("https://example.com/")
        # Should sleep at least 7 seconds (Retry-After value)
        slept = mock_sleep.call_args_list[0][0][0]
        assert slept >= 7.0

    def test_non_429_not_retried(self):
        c = _client()
        c._session.get.return_value = _resp(403)
        resp = c.get("https://example.com/")
        assert resp.status_code == 403
        assert c._session.get.call_count == 1

    def test_request_count_includes_retries(self):
        c = _client()
        first  = _resp(429, {"Retry-After": "0"})
        second = _resp(200)
        c._session.get.side_effect = [first, second]
        with patch("commonhuman_core.http.client.time.sleep"):
            c.get("https://example.com/")
        assert c.request_count == 2

    def test_429_no_retry_after_uses_default_backoff(self):
        c = _client()
        c._session.get.side_effect = [_resp(429), _resp(200)]
        with patch("commonhuman_core.http.client.time.sleep") as mock_sleep:
            c.get("https://example.com/")
        assert mock_sleep.call_args_list[0][0][0] == 5.0

    def test_invalid_retry_after_falls_back_to_default(self):
        c = _client()
        c._session.get.side_effect = [_resp(429, {"Retry-After": "bad"}), _resp(200)]
        with patch("commonhuman_core.http.client.time.sleep") as mock_sleep:
            c.get("https://example.com/")
        assert mock_sleep.call_args_list[0][0][0] == 5.0

    def test_all_retries_exhausted_returns_429(self):
        c = _client()
        c._session.get.side_effect = [_resp(429), _resp(429), _resp(429)]
        with patch("commonhuman_core.http.client.time.sleep"):
            resp = c.get("https://example.com/")
        assert resp.status_code == 429
        assert c.request_count == 3


# ---------------------------------------------------------------------------
# Injection helpers
# ---------------------------------------------------------------------------


class TestInjectGet:
    def test_replaces_existing_param(self):
        c = _client()
        c._session.get.return_value = _resp()
        c.inject_get("https://example.com/?q=original&page=1", "q", "PAYLOAD")
        called_url = c._session.get.call_args[0][0]
        assert "PAYLOAD" in called_url
        assert "original" not in called_url
        assert "page=1" in called_url

    def test_adds_missing_param(self):
        c = _client()
        c._session.get.return_value = _resp()
        c.inject_get("https://example.com/page", "q", "PAYLOAD")
        called_url = c._session.get.call_args[0][0]
        assert "q=PAYLOAD" in called_url


class TestInjectPost:
    def test_sends_param_in_body(self):
        c = _client()
        c._session.post.return_value = _resp()
        c.inject_post("https://example.com/", "user", "PAYLOAD")
        kwargs = c._session.post.call_args[1]
        assert kwargs["data"]["user"] == "PAYLOAD"

    def test_merges_base_data(self):
        c = _client()
        c._session.post.return_value = _resp()
        c.inject_post("https://example.com/", "q", "X", base_data={"hidden": "val"})
        kwargs = c._session.post.call_args[1]
        assert kwargs["data"]["hidden"] == "val"
        assert kwargs["data"]["q"] == "X"


class TestInjectPostJson:
    def test_sends_json_body(self):
        c = _client()
        c._session.post.return_value = _resp()
        c.inject_post_json("https://example.com/", "query", "PAYLOAD")
        kwargs = c._session.post.call_args[1]
        assert kwargs["json"]["query"] == "PAYLOAD"

    def test_merges_base_data(self):
        c = _client()
        c._session.post.return_value = _resp()
        c.inject_post_json("https://example.com/", "q", "X", base_data={"key": "val"})
        kwargs = c._session.post.call_args[1]
        assert kwargs["json"]["key"] == "val"


class TestInjectPath:
    def test_replaces_segment_at_index(self):
        c = _client()
        c._session.get.return_value = _resp()
        c.inject_path("https://example.com/api/user/123", 3, "PAYLOAD")
        called_url = c._session.get.call_args[0][0]
        assert "PAYLOAD" in called_url
        assert "123" not in called_url

    def test_minus_one_appends_segment(self):
        c = _client()
        c._session.get.return_value = _resp()
        c.inject_path("https://example.com/page", -1, "PAYLOAD")
        called_url = c._session.get.call_args[0][0]
        assert "PAYLOAD" in called_url

    def test_out_of_range_index_no_crash(self):
        c = _client()
        c._session.get.return_value = _resp()
        c.inject_path("https://example.com/", 99, "PAYLOAD")


class TestInjectCookie:
    def test_passes_cookie_in_request(self):
        c = _client()
        c._session.get.return_value = _resp()
        c.inject_cookie("https://example.com/", "session", "PAYLOAD")
        kwargs = c._session.get.call_args[1]
        assert kwargs["cookies"]["session"] == "PAYLOAD"


class TestInjectHeader:
    def test_passes_header_in_request(self):
        c = _client()
        c._session.get.return_value = _resp()
        c.inject_header("https://example.com/", "X-Forwarded-For", "127.0.0.1")
        kwargs = c._session.get.call_args[1]
        assert kwargs["headers"]["X-Forwarded-For"] == "127.0.0.1"


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------


class TestUrlUtilities:
    def test_get_params(self):
        params = HttpClient.get_params("https://example.com/?a=1&b=2")
        assert set(params) == {"a", "b"}

    def test_get_params_empty(self):
        assert HttpClient.get_params("https://example.com/") == []

    def test_get_base_url(self):
        assert HttpClient.get_base_url("https://example.com/path?q=1") == "https://example.com"

    def test_same_origin_true(self):
        assert HttpClient.same_origin(
            "https://example.com/a", "https://example.com/b"
        )

    def test_same_origin_false_different_host(self):
        assert not HttpClient.same_origin(
            "https://example.com/", "https://other.com/"
        )

    def test_same_origin_false_different_scheme(self):
        assert not HttpClient.same_origin(
            "http://example.com/", "https://example.com/"
        )


# ---------------------------------------------------------------------------
# Delay
# ---------------------------------------------------------------------------


class TestDelay:
    def test_delay_applied_before_get(self):
        c = _client(delay=0.1)
        c._session.get.return_value = _resp()
        with patch("commonhuman_core.http.client.time.sleep") as mock_sleep:
            c.get("https://example.com/")
        mock_sleep.assert_called_once_with(0.1)

    def test_no_delay_on_head(self):
        c = _client(delay=0.5)
        c._session.head.return_value = _resp()
        with patch("commonhuman_core.http.client.time.sleep") as mock_sleep:
            c.head("https://example.com/")
        mock_sleep.assert_not_called()

    def test_post_with_delay_sleeps(self):
        c = _client(delay=0.3)
        c._session.post.return_value = _resp()
        with patch("commonhuman_core.http.client.time.sleep") as mock_sleep:
            c.post("https://example.com/", data={"x": "1"})
        mock_sleep.assert_called_once_with(0.3)


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_calls_session_close(self):
        c = _client()
        c.close()
        c._session.close.assert_called_once()
