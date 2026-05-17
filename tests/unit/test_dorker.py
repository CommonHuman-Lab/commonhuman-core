# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Unit tests for commonhuman_core.dorker — all network calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from commonhuman_core.dorker import (
    DorkEngine,
    dork,
    _has_params,
    _dork_ddg,
    _dork_bing,
    _dork_yahoo,
)


# ---------------------------------------------------------------------------
# DorkEngine constants
# ---------------------------------------------------------------------------

class TestDorkEngine:
    def test_ddg_value(self):
        assert DorkEngine.DDG == "ddg"

    def test_bing_value(self):
        assert DorkEngine.BING == "bing"

    def test_yahoo_value(self):
        assert DorkEngine.YAHOO == "yahoo"

    def test_all_value(self):
        assert DorkEngine.ALL == "all"


# ---------------------------------------------------------------------------
# _has_params
# ---------------------------------------------------------------------------

class TestHasParams:
    def test_url_with_params(self):
        assert _has_params("https://example.com/search?q=xss") is True

    def test_url_without_params(self):
        assert _has_params("https://example.com/about") is False

    def test_empty_query_string(self):
        assert _has_params("https://example.com/page?") is False


# ---------------------------------------------------------------------------
# _dork_ddg
# ---------------------------------------------------------------------------

def _mock_response(text: str, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


_DDG_HTML = '''
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Ftarget.com%2Fsearch%3Fq%3Dxss&rut=xyz">Result 1</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fother.com%2Fpage%3Fid%3D1&rut=abc">Result 2</a>
</body></html>
'''

_BING_HTML = '''
<html><body>
<a href="https://target.com/q?search=test" class="tilk">Result</a>
</body></html>
'''

_YAHOO_HTML = '''
<html><body>
<a href="/RU=https://target.com/find?q=vuln/RK=2/xxx">Result</a>
</body></html>
'''


class TestDorkDdg:
    def test_returns_urls_with_params(self):
        with patch("commonhuman_core.dorker.requests.get",
                   return_value=_mock_response(_DDG_HTML)):
            urls = _dork_ddg("site:example.com", 10, {}, 5)
        assert any("search?q=xss" in u for u in urls)

    def test_returns_empty_on_request_error(self):
        import requests
        with patch("commonhuman_core.dorker.requests.get",
                   side_effect=requests.RequestException("timeout")):
            urls = _dork_ddg("query", 10, {}, 5)
        assert urls == []

    def test_respects_max_results(self):
        with patch("commonhuman_core.dorker.requests.get",
                   return_value=_mock_response(_DDG_HTML)):
            urls = _dork_ddg("query", 1, {}, 5)
        assert len(urls) <= 1


class TestDorkBing:
    def test_returns_urls_with_params(self):
        with patch("commonhuman_core.dorker.requests.get",
                   return_value=_mock_response(_BING_HTML)):
            urls = _dork_bing("site:example.com", 10, {}, 5)
        assert any("search=test" in u for u in urls)

    def test_returns_empty_on_request_error(self):
        import requests
        with patch("commonhuman_core.dorker.requests.get",
                   side_effect=requests.RequestException("refused")):
            urls = _dork_bing("query", 10, {}, 5)
        assert urls == []


class TestDorkYahoo:
    def test_returns_urls_with_params(self):
        with patch("commonhuman_core.dorker.requests.get",
                   return_value=_mock_response(_YAHOO_HTML)):
            urls = _dork_yahoo("site:example.com", 10, {}, 5)
        assert any("q=vuln" in u for u in urls)

    def test_returns_empty_on_request_error(self):
        import requests
        with patch("commonhuman_core.dorker.requests.get",
                   side_effect=requests.RequestException("error")):
            urls = _dork_yahoo("query", 10, {}, 5)
        assert urls == []


# ---------------------------------------------------------------------------
# dork() — integration
# ---------------------------------------------------------------------------

class TestDork:
    def _patch_engines(self, ddg=None, bing=None, yahoo=None):
        ddg   = ddg   or ["https://a.com?q=1"]
        bing  = bing  or ["https://b.com?q=2"]
        yahoo = yahoo or ["https://c.com?q=3"]
        return (
            patch("commonhuman_core.dorker._dork_ddg",   return_value=ddg),
            patch("commonhuman_core.dorker._dork_bing",  return_value=bing),
            patch("commonhuman_core.dorker._dork_yahoo", return_value=yahoo),
        )

    def test_ddg_engine_calls_only_ddg(self):
        with patch("commonhuman_core.dorker._dork_ddg",   return_value=["https://x.com?q=1"]) as m_ddg, \
             patch("commonhuman_core.dorker._dork_bing",  return_value=[]) as m_bing, \
             patch("commonhuman_core.dorker._dork_yahoo", return_value=[]) as m_yahoo:
            result = dork("query", engine=DorkEngine.DDG)
        m_ddg.assert_called_once()
        m_bing.assert_not_called()
        m_yahoo.assert_not_called()
        assert "https://x.com?q=1" in result

    def test_all_engine_calls_all_three(self):
        p_ddg, p_bing, p_yahoo = self._patch_engines()
        with p_ddg as m_ddg, p_bing as m_bing, p_yahoo as m_yahoo:
            result = dork("query", engine=DorkEngine.ALL)
        m_ddg.assert_called_once()
        m_bing.assert_called_once()
        m_yahoo.assert_called_once()
        assert len(result) == 3

    def test_deduplication_across_engines(self):
        shared = "https://shared.com?q=1"
        p_ddg  = patch("commonhuman_core.dorker._dork_ddg",   return_value=[shared])
        p_bing = patch("commonhuman_core.dorker._dork_bing",  return_value=[shared])
        p_yahoo = patch("commonhuman_core.dorker._dork_yahoo", return_value=[])
        with p_ddg, p_bing, p_yahoo:
            result = dork("query", engine=DorkEngine.ALL)
        assert result.count(shared) == 1

    def test_single_engine_respects_max_results(self):
        many = [f"https://x.com?q={i}" for i in range(50)]
        with patch("commonhuman_core.dorker._dork_ddg", return_value=many):
            result = dork("query", max_results=5, engine=DorkEngine.DDG)
        assert len(result) <= 5

    def test_returns_empty_list_on_all_failures(self):
        p_ddg  = patch("commonhuman_core.dorker._dork_ddg",   return_value=[])
        p_bing = patch("commonhuman_core.dorker._dork_bing",  return_value=[])
        p_yahoo = patch("commonhuman_core.dorker._dork_yahoo", return_value=[])
        with p_ddg, p_bing, p_yahoo:
            result = dork("query", engine=DorkEngine.DDG)
        assert result == []

    def test_unknown_engine_returns_empty(self):
        result = dork("query", engine="unknown_engine")
        assert result == []
