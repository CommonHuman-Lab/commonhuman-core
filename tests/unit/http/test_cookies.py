# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Tests for commonhuman_core.http._cookies."""

from commonhuman_core.http import parse_cookie_string, parse_post_data


class TestParseCookieString:
    def test_single_pair(self):
        assert parse_cookie_string("session=abc123") == {"session": "abc123"}

    def test_multiple_pairs(self):
        result = parse_cookie_string("a=1; b=2; c=3")
        assert result == {"a": "1", "b": "2", "c": "3"}

    def test_json_input(self):
        result = parse_cookie_string('{"token": "xyz", "user": "bob"}')
        assert result == {"token": "xyz", "user": "bob"}

    def test_invalid_json_falls_back_to_kv(self):
        result = parse_cookie_string("{not json; a=1}")
        assert "a" in result

    def test_strips_whitespace(self):
        result = parse_cookie_string("  a = 1 ;  b = 2 ")
        assert result["a"] == "1"
        assert result["b"] == "2"

    def test_value_with_equals(self):
        result = parse_cookie_string("token=abc=def")
        assert result["token"] == "abc=def"

    def test_empty_string_returns_empty(self):
        assert parse_cookie_string("") == {}


class TestParsePostData:
    def test_urlencoded(self):
        assert parse_post_data("user=alice&pass=secret") == {"user": "alice", "pass": "secret"}

    def test_json_body(self):
        result = parse_post_data('{"user": "alice", "age": 30}')
        assert result == {"user": "alice", "age": "30"}

    def test_invalid_json_falls_back_to_urlencode(self):
        result = parse_post_data("key=value")
        assert result == {"key": "value"}

    def test_blank_values_preserved(self):
        result = parse_post_data("a=&b=x")
        assert result["a"] == ""
        assert result["b"] == "x"

    def test_non_dict_json_falls_back(self):
        result = parse_post_data("[1, 2, 3]")
        assert isinstance(result, dict)

    def test_json_syntax_error_falls_back(self):
        result = parse_post_data("{bad: json}")
        assert isinstance(result, dict)
