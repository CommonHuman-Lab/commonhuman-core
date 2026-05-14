# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Tests for commonhuman_core.auth — all network I/O is mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest

from commonhuman_core.auth import (
    AuthResult,
    _FormParser,
    bearer_login,
    extract_csrf,
    form_login,
)
from commonhuman_core.http import HttpClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_resp(
    text: str = "",
    status: int = 200,
    cookies: dict | None = None,
    json_data: dict | None = None,
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    resp.json.side_effect = (
        (lambda: json_data) if json_data is not None
        else (lambda: (_ for _ in ()).throw(ValueError("no json")))
    )
    return resp


def _make_session(
    get_resp=None,
    post_resp=None,
    cookies: dict | None = None,
) -> MagicMock:
    s = MagicMock()
    s.get.return_value  = get_resp or _make_session_resp()
    s.post.return_value = post_resp or _make_session_resp()
    # Make cookies iterable like requests.Session.cookies
    cookie_dict = cookies or {}
    s.cookies.items.return_value = list(cookie_dict.items())
    return s


# ---------------------------------------------------------------------------
# AuthResult
# ---------------------------------------------------------------------------

class TestAuthResult:
    def test_empty_when_no_cookies_no_headers(self):
        assert AuthResult().is_empty() is True

    def test_not_empty_with_cookies(self):
        assert AuthResult(cookies="session=abc").is_empty() is False

    def test_not_empty_with_headers(self):
        assert AuthResult(headers={"Authorization": "Bearer x"}).is_empty() is False

    def test_both_populated(self):
        r = AuthResult(cookies="a=1", headers={"X-Token": "t"})
        assert r.is_empty() is False


# ---------------------------------------------------------------------------
# form_login — success paths
# ---------------------------------------------------------------------------

class TestFormLoginSuccess:
    def _client_with_session(self, session: MagicMock) -> HttpClient:
        c = HttpClient(timeout=5)
        c._session = session
        return c

    def test_cookies_returned_on_success(self):
        sess = _make_session(
            get_resp=_make_session_resp(text="<form><input name='u'></form>"),
            post_resp=_make_session_resp(text="OK"),
            cookies={"session": "abc", "csrf": "tok"},
        )
        client = self._client_with_session(sess)
        result = form_login("http://target.com/login", "user", "pass", client=client)
        assert "session=abc" in result.cookies
        assert "csrf=tok" in result.cookies

    def test_json_token_access_token_populates_header(self):
        sess = _make_session(
            get_resp=_make_session_resp(text="<form></form>"),
            post_resp=_make_session_resp(json_data={"access_token": "JWT_TOK"}),
            cookies={},
        )
        client = self._client_with_session(sess)
        result = form_login("http://target.com/login", "u", "p", client=client)
        assert result.headers.get("Authorization") == "Bearer JWT_TOK"

    def test_json_token_fallback_keys(self):
        for key in ("token", "accessToken", "jwt", "id_token"):
            sess = _make_session(
                get_resp=_make_session_resp(text="<form></form>"),
                post_resp=_make_session_resp(json_data={key: "MY_TOKEN"}),
                cookies={},
            )
            client = self._client_with_session(sess)
            result = form_login("http://target.com/login", "u", "p", client=client)
            assert result.headers.get("Authorization") == "Bearer MY_TOKEN", f"failed for key={key}"

    def test_csrf_hidden_field_included_in_post(self):
        html = (
            '<form action="/login" method="post">'
            '<input type="hidden" name="csrf_token" value="CSRFVAL">'
            '<input type="text" name="username">'
            '</form>'
        )
        sess = _make_session(
            get_resp=_make_session_resp(text=html),
            post_resp=_make_session_resp(text="OK"),
            cookies={"s": "1"},
        )
        client = self._client_with_session(sess)
        form_login("http://target.com/login", "alice", "secret", client=client)
        _, call_kwargs = sess.post.call_args
        posted_data = call_kwargs.get("data", {})
        assert posted_data.get("csrf_token") == "CSRFVAL"

    def test_credentials_in_post_body(self):
        sess = _make_session(
            get_resp=_make_session_resp(text="<form></form>"),
            post_resp=_make_session_resp(text="OK"),
            cookies={"s": "1"},
        )
        client = self._client_with_session(sess)
        form_login(
            "http://target.com/login", "alice", "s3cr3t",
            username_field="email", password_field="pwd",
            client=client,
        )
        _, call_kwargs = sess.post.call_args
        posted_data = call_kwargs.get("data", {})
        assert posted_data.get("email") == "alice"
        assert posted_data.get("pwd") == "s3cr3t"

    def test_extra_fields_included_in_post(self):
        sess = _make_session(
            get_resp=_make_session_resp(text="<form></form>"),
            post_resp=_make_session_resp(text="OK"),
            cookies={"s": "1"},
        )
        client = self._client_with_session(sess)
        form_login(
            "http://target.com/login", "u", "p",
            extra_fields={"remember": "1", "mfa": "123456"},
            client=client,
        )
        _, call_kwargs = sess.post.call_args
        posted_data = call_kwargs.get("data", {})
        assert posted_data.get("remember") == "1"
        assert posted_data.get("mfa") == "123456"

    def test_relative_action_resolved_to_absolute(self):
        html = '<form action="/auth/do-login"><input name="user"></form>'
        sess = _make_session(
            get_resp=_make_session_resp(text=html),
            post_resp=_make_session_resp(text="OK"),
            cookies={"s": "1"},
        )
        client = self._client_with_session(sess)
        form_login("http://target.com/login", "u", "p", client=client)
        args, _ = sess.post.call_args
        assert args[0] == "http://target.com/auth/do-login"

    def test_empty_action_posts_to_login_url(self):
        sess = _make_session(
            get_resp=_make_session_resp(text="<form></form>"),
            post_resp=_make_session_resp(text="OK"),
            cookies={"s": "1"},
        )
        client = self._client_with_session(sess)
        form_login("http://target.com/login", "u", "p", client=client)
        args, _ = sess.post.call_args
        assert args[0] == "http://target.com/login"

    def test_creates_own_client_when_none_passed(self):
        with patch("commonhuman_core.auth.HttpClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            mock_instance._session.get.return_value = _make_session_resp(text="<form></form>")
            mock_instance._session.post.return_value = _make_session_resp(text="OK")
            mock_instance._session.cookies.items.return_value = []
            form_login("http://target.com/login", "u", "p")
            MockClient.assert_called_once()


# ---------------------------------------------------------------------------
# form_login — failure paths
# ---------------------------------------------------------------------------

class TestFormLoginFailure:
    def _client_with_session(self, session: MagicMock) -> HttpClient:
        c = HttpClient(timeout=5)
        c._session = session
        return c

    def test_get_failure_returns_empty(self):
        sess = MagicMock()
        sess.get.side_effect = OSError("connection refused")
        client = self._client_with_session(sess)
        result = form_login("http://target.com/login", "u", "p", client=client)
        assert result.is_empty()

    def test_post_failure_returns_empty(self):
        sess = _make_session(
            get_resp=_make_session_resp(text="<form></form>"),
        )
        sess.post.side_effect = OSError("timeout")
        sess.cookies.items.return_value = []
        client = self._client_with_session(sess)
        result = form_login("http://target.com/login", "u", "p", client=client)
        assert result.is_empty()

    def test_empty_result_when_no_cookies_no_token(self):
        sess = _make_session(
            get_resp=_make_session_resp(text="<form></form>"),
            post_resp=_make_session_resp(text="OK"),
            cookies={},
        )
        client = self._client_with_session(sess)
        result = form_login("http://target.com/login", "u", "p", client=client)
        assert result.is_empty()

    def test_non_string_token_not_used(self):
        sess = _make_session(
            get_resp=_make_session_resp(text="<form></form>"),
            post_resp=_make_session_resp(json_data={"access_token": 12345}),
            cookies={},
        )
        client = self._client_with_session(sess)
        result = form_login("http://target.com/login", "u", "p", client=client)
        assert "Authorization" not in result.headers


# ---------------------------------------------------------------------------
# bearer_login
# ---------------------------------------------------------------------------

class TestBearerLogin:
    def _client_with_session(self, session: MagicMock) -> HttpClient:
        c = HttpClient(timeout=5)
        c._session = session
        return c

    def test_access_token_returned(self):
        sess = MagicMock()
        sess.post.return_value = _make_session_resp(json_data={"access_token": "MY_TOKEN"})
        client = self._client_with_session(sess)
        result = bearer_login("http://auth.com/token", "cid", "csec", client=client)
        assert result.headers.get("Authorization") == "Bearer MY_TOKEN"

    def test_token_fallback_key(self):
        sess = MagicMock()
        sess.post.return_value = _make_session_resp(json_data={"token": "TOK2"})
        client = self._client_with_session(sess)
        result = bearer_login("http://auth.com/token", "cid", "csec", client=client)
        assert result.headers.get("Authorization") == "Bearer TOK2"

    def test_id_token_fallback_key(self):
        sess = MagicMock()
        sess.post.return_value = _make_session_resp(json_data={"id_token": "ID_T"})
        client = self._client_with_session(sess)
        result = bearer_login("http://auth.com/token", "cid", "csec", client=client)
        assert result.headers.get("Authorization") == "Bearer ID_T"

    def test_no_token_field_returns_empty(self):
        sess = MagicMock()
        sess.post.return_value = _make_session_resp(json_data={"status": "ok"})
        client = self._client_with_session(sess)
        result = bearer_login("http://auth.com/token", "cid", "csec", client=client)
        assert result.is_empty()

    def test_network_error_returns_empty(self):
        sess = MagicMock()
        sess.post.side_effect = OSError("timeout")
        client = self._client_with_session(sess)
        result = bearer_login("http://auth.com/token", "cid", "csec", client=client)
        assert result.is_empty()

    def test_creates_own_client_when_none_passed(self):
        with patch("commonhuman_core.auth.HttpClient") as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            mock_instance._session.post.return_value = _make_session_resp(
                json_data={"access_token": "TOK"}
            )
            bearer_login("http://auth.com/token", "cid", "csec")
            MockClient.assert_called_once()

    def test_custom_grant_type_sent(self):
        sess = MagicMock()
        sess.post.return_value = _make_session_resp(json_data={"access_token": "T"})
        client = self._client_with_session(sess)
        bearer_login("http://auth.com/token", "cid", "csec",
                     grant_type="password", client=client)
        _, call_kwargs = sess.post.call_args
        assert call_kwargs["data"]["grant_type"] == "password"


# ---------------------------------------------------------------------------
# extract_csrf
# ---------------------------------------------------------------------------

class TestExtractCsrf:
    def test_finds_csrf_token_field(self):
        html = '<form><input type="hidden" name="csrf_token" value="MYCSRF"></form>'
        assert extract_csrf(html) == "MYCSRF"

    def test_finds_csrfmiddlewaretoken(self):
        html = '<form><input type="hidden" name="csrfmiddlewaretoken" value="DJANGO_TOK"></form>'
        assert extract_csrf(html) == "DJANGO_TOK"

    def test_finds_xsrf_token(self):
        html = '<form><input name="xsrf_token" value="XSRF_VAL"></form>'
        assert extract_csrf(html) == "XSRF_VAL"

    def test_finds_authenticity_token(self):
        html = '<form><input type="hidden" name="authenticity_token" value="RAILS_TOK"></form>'
        assert extract_csrf(html) == "RAILS_TOK"

    def test_case_insensitive_name_match(self):
        # HTML attribute values preserve case; auth.py lowercases the field name for lookup
        html = '<form><input name="CSRF_TOKEN" value="VAL"></form>'
        assert extract_csrf(html) == "VAL"

    def test_no_csrf_field_returns_none(self):
        html = '<form><input name="username"><input name="password"></form>'
        assert extract_csrf(html) is None

    def test_empty_value_returns_none(self):
        html = '<form><input name="csrf_token" value=""></form>'
        assert extract_csrf(html) is None


# ---------------------------------------------------------------------------
# _FormParser
# ---------------------------------------------------------------------------

class TestFormParser:
    def test_action_extracted(self):
        p = _FormParser()
        p.feed('<form action="/login"><input name="u"></form>')
        assert p.action == "/login"

    def test_method_extracted(self):
        p = _FormParser()
        p.feed('<form method="GET"><input name="q"></form>')
        assert p.method == "get"

    def test_default_method_is_post(self):
        p = _FormParser()
        p.feed('<form><input name="u"></form>')
        assert p.method == "post"

    def test_text_field_captured(self):
        p = _FormParser()
        p.feed('<form><input type="text" name="user" value="default"></form>')
        assert p.fields.get("user") == "default"

    def test_hidden_field_captured(self):
        p = _FormParser()
        p.feed('<form><input type="hidden" name="token" value="XYZ"></form>')
        assert p.fields.get("token") == "XYZ"

    def test_submit_button_skipped(self):
        p = _FormParser()
        p.feed('<form><input type="submit" name="submit" value="Login"></form>')
        assert "submit" not in p.fields

    def test_button_type_skipped(self):
        p = _FormParser()
        p.feed('<form><input type="button" name="btn" value="Go"></form>')
        assert "btn" not in p.fields

    def test_image_type_skipped(self):
        p = _FormParser()
        p.feed('<form><input type="image" name="img"></form>')
        assert "img" not in p.fields

    def test_reset_type_skipped(self):
        p = _FormParser()
        p.feed('<form><input type="reset" name="r"></form>')
        assert "r" not in p.fields

    def test_file_type_skipped(self):
        p = _FormParser()
        p.feed('<form><input type="file" name="f"></form>')
        assert "f" not in p.fields

    def test_input_without_name_skipped(self):
        p = _FormParser()
        p.feed('<form><input type="text" value="ignored"></form>')
        assert p.fields == {}

    def test_only_first_form_parsed(self):
        p = _FormParser()
        p.feed(
            '<form action="/first"><input name="a" value="1"></form>'
            '<form action="/second"><input name="b" value="2"></form>'
        )
        assert p.action == "/first"
        assert "a" in p.fields
        assert "b" not in p.fields

    def test_input_outside_form_ignored(self):
        p = _FormParser()
        p.feed('<input name="outside" value="ignored"><form><input name="inside" value="ok"></form>')
        assert "outside" not in p.fields
        assert p.fields.get("inside") == "ok"

    def test_empty_value_defaults_to_empty_string(self):
        p = _FormParser()
        p.feed('<form><input name="noval"></form>')
        assert p.fields.get("noval") == ""

    def test_action_defaults_to_empty_string(self):
        p = _FormParser()
        p.feed('<form><input name="x"></form>')
        assert p.action == ""
