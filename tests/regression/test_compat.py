# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""
Regression tests: verify backward-compatibility contracts for both tools.

These tests import from the tool packages directly and confirm that the
migration to commonhuman-core did not change any public API surface.
"""

from __future__ import annotations

import pytest

from commonhuman_core.http import HttpClient
from commonhuman_core.crawler import CrawlResult, FormTarget


# ---------------------------------------------------------------------------
# CrawlResult / FormTarget field contracts
# ---------------------------------------------------------------------------


class TestCrawlResultContract:
    def test_fields_present(self):
        r = CrawlResult()
        assert hasattr(r, "visited_urls")
        assert hasattr(r, "form_targets")
        assert hasattr(r, "url_params")
        assert hasattr(r, "page_sources")

    def test_defaults_are_empty_collections(self):
        r = CrawlResult()
        assert r.visited_urls == []
        assert r.form_targets == []
        assert r.url_params   == []
        assert r.page_sources == {}


class TestFormTargetContract:
    def test_fields_present(self):
        f = FormTarget(method="GET", params={"q": ""}, action="https://x.com/")
        assert hasattr(f, "method")
        assert hasattr(f, "params")
        assert hasattr(f, "action")
        assert hasattr(f, "base_data")

    def test_base_data_defaults_to_empty(self):
        f = FormTarget(method="POST", params={"q": ""}, action="https://x.com/")
        assert f.base_data == {}


# ---------------------------------------------------------------------------
# HttpClient API surface (both tools depend on these)
# ---------------------------------------------------------------------------


class TestHttpClientContract:
    def test_has_get(self):           assert callable(HttpClient.get)
    def test_has_post(self):          assert callable(HttpClient.post)
    def test_has_head(self):          assert callable(HttpClient.head)
    def test_has_inject_get(self):    assert callable(HttpClient.inject_get)
    def test_has_inject_post(self):   assert callable(HttpClient.inject_post)
    def test_has_inject_post_json(self): assert callable(HttpClient.inject_post_json)
    def test_has_inject_path(self):   assert callable(HttpClient.inject_path)
    def test_has_inject_cookie(self): assert callable(HttpClient.inject_cookie)
    def test_has_inject_header(self): assert callable(HttpClient.inject_header)
    def test_has_get_params(self):    assert callable(HttpClient.get_params)
    def test_has_get_base_url(self):  assert callable(HttpClient.get_base_url)
    def test_has_same_origin(self):   assert callable(HttpClient.same_origin)
    def test_has_close(self):         assert callable(HttpClient.close)

    def test_request_count_attribute(self):
        c = HttpClient()
        assert c.request_count == 0


# ---------------------------------------------------------------------------
# StingXSS Injector is an HttpClient subclass
# ---------------------------------------------------------------------------


stingxss = pytest.importorskip("stingxss", reason="stingxss not installed in this venv")


class TestStingXSSInjectorIsSubclass:
    def test_injector_inherits_http_client(self):
        from stingxss.engine.http.injector import Injector
        assert issubclass(Injector, HttpClient)

    def test_injector_has_probe_reflection(self):
        from stingxss.engine.http.injector import Injector
        assert callable(Injector.probe_reflection)

    def test_injector_has_probe_header_reflection(self):
        from stingxss.engine.http.injector import Injector
        assert callable(Injector.probe_header_reflection)

    def test_stingxss_crawler_is_core_crawl(self):
        from stingxss.engine import crawler as sting_crawler
        from commonhuman_core import crawler as core_crawler
        assert sting_crawler.crawl is core_crawler.crawl


# ---------------------------------------------------------------------------
# BreachSQL Injector is HttpClient (or a subclass)
# ---------------------------------------------------------------------------


breachsql = pytest.importorskip("breachsql", reason="breachsql not installed in this venv")


class TestBreachSQLInjectorIsHttpClient:
    def test_injector_is_http_client(self):
        from breachsql.engine.http.injector import Injector
        assert Injector is HttpClient or issubclass(Injector, HttpClient)

    def test_breachsql_crawler_is_core_crawl(self):
        from breachsql.engine import crawler as breach_crawler
        from commonhuman_core import crawler as core_crawler
        assert breach_crawler.crawl is core_crawler.crawl
