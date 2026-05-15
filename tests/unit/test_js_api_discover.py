# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Tests for commonhuman_core.js_api_discover."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests
import pytest

from commonhuman_core.js_api_discover import (
    _collect_bundle_urls,
    _extract_indirect_concat,
    _extract_method_templates,
    _extract_static_paths,
    _resolve_template,
    js_api_discover,
)


# ---------------------------------------------------------------------------
# _resolve_template
# ---------------------------------------------------------------------------

class TestResolveTemplate:
    def test_host_variable_replaced(self):
        result = _resolve_template("${baseUrl}/rest/foo", "https://example.com")
        assert result == "https://example.com/rest/foo"

    def test_server_variable_replaced(self):
        result = _resolve_template("${apiServer}/api/bar", "https://x.com")
        assert result == "https://x.com/api/bar"

    def test_origin_variable_replaced(self):
        result = _resolve_template("${origin}/rest/baz", "https://x.com")
        assert result == "https://x.com/rest/baz"

    def test_id_variable_replaced_with_1(self):
        result = _resolve_template("/rest/items/${itemId}", "https://x.com")
        assert result == "/rest/items/1"

    def test_page_variable_replaced_with_1(self):
        result = _resolve_template("/rest/list?page=${page}&limit=${limit}", "https://x.com")
        assert result == "/rest/list?page=1&limit=1"

    def test_generic_variable_replaced_with_test(self):
        result = _resolve_template("/rest/search?q=${query}", "https://x.com")
        assert result == "/rest/search?q=test"

    def test_mixed_variables_all_replaced(self):
        result = _resolve_template("${host}/rest/items/${id}?q=${q}", "https://x.com")
        assert result == "https://x.com/rest/items/1?q=test"

    def test_no_variables_unchanged(self):
        result = _resolve_template("/rest/foo/bar", "https://x.com")
        assert result == "/rest/foo/bar"


# ---------------------------------------------------------------------------
# _extract_method_templates
# ---------------------------------------------------------------------------

class TestExtractMethodTemplates:
    def test_get_template_literal(self):
        js = 'this.http.get(`${this.host}/rest/products/search?q=${term}`)'
        results = _extract_method_templates(js, "http://example.com")
        assert len(results) == 1
        method, url, template = results[0]
        assert method == "GET"
        assert "/rest/products/search" in url

    def test_post_template_literal(self):
        js = 'this.http.post(`${base}/api/users`, body)'
        results = _extract_method_templates(js, "http://example.com")
        assert results[0][0] == "POST"

    def test_put_template_literal(self):
        js = 'http.put(`${host}/api/items/${id}`, data)'
        results = _extract_method_templates(js, "http://example.com")
        assert results[0][0] == "PUT"

    def test_patch_template_literal(self):
        js = 'this.http.patch(`${host}/rest/users/${id}`, data)'
        results = _extract_method_templates(js, "http://example.com")
        assert results[0][0] == "PATCH"

    def test_delete_template_literal(self):
        js = 'this.http.delete(`${host}/rest/items/${id}`)'
        results = _extract_method_templates(js, "http://example.com")
        assert results[0][0] == "DELETE"

    def test_case_insensitive_method(self):
        js = 'this.http.GET(`${host}/rest/foo`)'
        results = _extract_method_templates(js, "http://example.com")
        assert results[0][0] == "GET"

    def test_relative_url_gets_origin_prepended(self):
        js = 'http.get(`/rest/products`)'
        results = _extract_method_templates(js, "http://example.com")
        assert results[0][1].startswith("http://example.com")

    def test_template_already_has_full_url(self):
        js = 'http.get(`http://example.com/rest/products`)'
        results = _extract_method_templates(js, "http://example.com")
        # Full URL should not get origin prepended a second time
        assert results[0][1].count("http://") == 1

    def test_raw_template_preserved_as_third_element(self):
        js = 'get(`${host}/rest/foo/${id}`)'
        results = _extract_method_templates(js, "http://example.com")
        template = results[0][2]
        assert "/rest/foo/" in template

    def test_no_match_returns_empty(self):
        js = 'console.log("hello world")'
        assert _extract_method_templates(js, "http://example.com") == []

    def test_api_path_also_matched(self):
        js = 'get(`${base}/api/Products`)'
        results = _extract_method_templates(js, "http://example.com")
        assert len(results) == 1
        assert "/api/Products" in results[0][1]


# ---------------------------------------------------------------------------
# _extract_static_paths
# ---------------------------------------------------------------------------

class TestExtractStaticPaths:
    def test_rest_path_extracted(self):
        js = 'var url = "/rest/track-order";'
        results = _extract_static_paths(js, "http://example.com")
        urls = [r[1] for r in results]
        assert "http://example.com/rest/track-order" in urls

    def test_api_path_extracted(self):
        js = '"/api/Products"'
        results = _extract_static_paths(js, "http://example.com")
        urls = [r[1] for r in results]
        assert "http://example.com/api/Products" in urls

    def test_probe_variant_emitted_for_alpha_segment(self):
        js = '"/rest/track-order"'
        results = _extract_static_paths(js, "http://example.com")
        urls = [r[1] for r in results]
        assert "http://example.com/rest/track-order/1" in urls

    def test_no_probe_variant_when_numeric_last_segment(self):
        js = '"/rest/items/123"'
        results = _extract_static_paths(js, "http://example.com")
        urls = [r[1] for r in results]
        assert "http://example.com/rest/items/123" in urls
        assert "http://example.com/rest/items/123/1" not in urls

    def test_no_probe_variant_when_path_has_query_string(self):
        js = '"/rest/products/search?q=test"'
        results = _extract_static_paths(js, "http://example.com")
        urls = [r[1] for r in results]
        assert "http://example.com/rest/products/search?q=test" in urls
        assert not any(u.endswith("/1") for u in urls)

    def test_method_inferred_from_preceding_post(self):
        js = 'this.post( "/rest/user/login", body)'
        results = _extract_static_paths(js, "http://example.com")
        assert results[0][0] == "POST"

    def test_method_inferred_delete(self):
        js = 'delete( "/api/items/thing")'
        results = _extract_static_paths(js, "http://example.com")
        assert results[0][0] == "DELETE"

    def test_method_inferred_put(self):
        js = 'put( "/rest/resource/name")'
        results = _extract_static_paths(js, "http://example.com")
        assert results[0][0] == "PUT"

    def test_method_inferred_patch(self):
        js = 'patch( "/api/resource/name")'
        results = _extract_static_paths(js, "http://example.com")
        assert results[0][0] == "PATCH"

    def test_default_method_is_get(self):
        js = 'var url = "/rest/products";'
        results = _extract_static_paths(js, "http://example.com")
        assert results[0][0] == "GET"

    def test_js_extension_excluded(self):
        js = '"/rest/bundle.js"'
        assert _extract_static_paths(js, "http://example.com") == []

    def test_css_extension_excluded(self):
        js = '"/api/style.css"'
        assert _extract_static_paths(js, "http://example.com") == []

    def test_html_extension_excluded(self):
        js = '"/rest/page.html"'
        assert _extract_static_paths(js, "http://example.com") == []

    def test_png_extension_excluded(self):
        js = '"/rest/image.png"'
        assert _extract_static_paths(js, "http://example.com") == []

    def test_svg_extension_excluded(self):
        js = '"/api/icon.svg"'
        assert _extract_static_paths(js, "http://example.com") == []

    def test_raw_template_is_original_path(self):
        js = '"/rest/orders"'
        results = _extract_static_paths(js, "http://example.com")
        assert results[0][2] == "/rest/orders"

    def test_probe_variant_template_has_param_placeholder(self):
        js = '"/rest/orders"'
        results = _extract_static_paths(js, "http://example.com")
        templates = [r[2] for r in results]
        assert any("{param}" in t for t in templates)


# ---------------------------------------------------------------------------
# _extract_indirect_concat
# ---------------------------------------------------------------------------

class TestExtractIndirectConcat:
    def test_post_with_rest_base_and_suffix(self):
        js = (
            'host = this.hostServer + "/rest/user";\n'
            'this.http.post(this.host + "/login", body)'
        )
        results = _extract_indirect_concat(js, "http://example.com")
        assert len(results) == 1
        method, url, template = results[0]
        assert method == "POST"
        assert url == "http://example.com/rest/user/login"

    def test_get_with_api_base(self):
        js = (
            'apiBase = this.server + "/api/items";\n'
            'get(apiBase + "/search")'
        )
        results = _extract_indirect_concat(js, "http://example.com")
        assert results[0][0] == "GET"
        assert results[0][1] == "http://example.com/api/items/search"

    def test_this_prefix_on_assignment(self):
        js = (
            'this.base = "/rest/products";\n'
            'get(this.base + "/list")'
        )
        results = _extract_indirect_concat(js, "http://example.com")
        assert results[0][1] == "http://example.com/rest/products/list"

    def test_unknown_variable_skipped(self):
        js = 'this.http.get(this.unknown + "/search")'
        assert _extract_indirect_concat(js, "http://example.com") == []

    def test_non_rest_api_base_excluded(self):
        js = (
            'host = this.server + "/other/path";\n'
            'get(this.host + "/foo")'
        )
        assert _extract_indirect_concat(js, "http://example.com") == []

    def test_template_string_contains_varname_and_suffix(self):
        js = (
            'base = "/rest/user";\n'
            'post(this.base + "/login", body)'
        )
        results = _extract_indirect_concat(js, "http://example.com")
        _, _, tmpl = results[0]
        assert "base" in tmpl
        assert "/login" in tmpl

    def test_trailing_slash_on_base_not_doubled(self):
        js = (
            'root = "/rest/api/";\n'
            'get(root + "/items")'
        )
        results = _extract_indirect_concat(js, "http://example.com")
        # Only check the path segment — the scheme "http://" contains // legitimately
        path_part = results[0][1].split("://", 1)[1]
        assert "//" not in path_part


# ---------------------------------------------------------------------------
# _collect_bundle_urls
# ---------------------------------------------------------------------------

class TestCollectBundleUrls:
    def _make_session(self, responses: dict) -> MagicMock:
        sess = MagicMock()
        def _get(url, **kwargs):
            resp = MagicMock()
            resp.text = responses.get(url, "")
            resp.ok = True
            return resp
        sess.get.side_effect = _get
        return sess

    def test_script_src_collected(self):
        html = '<script src="/static/main.js"></script>'
        sess = self._make_session({
            "http://example.com/": html,
            "http://example.com/static/main.js": "",
        })
        urls = _collect_bundle_urls("http://example.com/", "http://example.com", sess, 20)
        assert "http://example.com/static/main.js" in urls

    def test_relative_src_gets_origin_prepended(self):
        html = '<script src="bundle.js"></script>'
        sess = self._make_session({
            "http://example.com/": html,
            "http://example.com/bundle.js": "",
        })
        urls = _collect_bundle_urls("http://example.com/", "http://example.com", sess, 20)
        assert "http://example.com/bundle.js" in urls

    def test_absolute_src_kept_as_is(self):
        html = '<script src="http://cdn.example.com/bundle.js"></script>'
        sess = self._make_session({
            "http://example.com/": html,
            "http://cdn.example.com/bundle.js": "",
        })
        urls = _collect_bundle_urls("http://example.com/", "http://example.com", sess, 20)
        assert "http://cdn.example.com/bundle.js" in urls

    def test_chunk_files_discovered_inside_bundle(self):
        html = '<script src="/main.js"></script>'
        sess = self._make_session({
            "http://example.com/": html,
            "http://example.com/main.js": 'loadChunk("chunk-ABC123XY.js")',
        })
        urls = _collect_bundle_urls("http://example.com/", "http://example.com", sess, 20)
        assert "http://example.com/chunk-ABC123XY.js" in urls

    def test_max_n_respected_for_script_tags(self):
        scripts = "".join(f'<script src="/bundle{i}.js"></script>' for i in range(10))
        responses = {"http://example.com/": scripts}
        for i in range(10):
            responses[f"http://example.com/bundle{i}.js"] = ""
        sess = self._make_session(responses)
        urls = _collect_bundle_urls("http://example.com/", "http://example.com", sess, 3)
        assert len(urls) <= 3

    def test_max_n_respected_during_chunk_discovery(self):
        # 1 script tag already fills slot; chunks should stop at max_n
        html = '<script src="/main.js"></script>'
        chunk_names = " ".join(f"chunk-{i:06X}AA.js" for i in range(10))
        sess = self._make_session({
            "http://example.com/": html,
            "http://example.com/main.js": chunk_names,
        })
        urls = _collect_bundle_urls("http://example.com/", "http://example.com", sess, 2)
        assert len(urls) == 2

    def test_oserror_on_html_fetch_returns_empty(self):
        sess = MagicMock()
        sess.get.side_effect = OSError("connection refused")
        urls = _collect_bundle_urls("http://example.com/", "http://example.com", sess, 20)
        assert urls == []

    def test_oserror_on_bundle_fetch_continues(self):
        html = '<script src="/main.js"></script>'
        sess = MagicMock()
        def _get(url, **kwargs):
            if url.endswith("/"):
                resp = MagicMock()
                resp.text = html
                return resp
            raise OSError("bundle fetch failed")
        sess.get.side_effect = _get
        # Should not raise — bundle OSError is swallowed
        urls = _collect_bundle_urls("http://example.com/", "http://example.com", sess, 20)
        assert "http://example.com/main.js" in urls

    def test_duplicate_srcs_deduplicated(self):
        html = '<script src="/main.js"></script><script src="/main.js"></script>'
        sess = self._make_session({
            "http://example.com/": html,
            "http://example.com/main.js": "",
        })
        urls = _collect_bundle_urls("http://example.com/", "http://example.com", sess, 20)
        assert urls.count("http://example.com/main.js") == 1


# ---------------------------------------------------------------------------
# js_api_discover — integration
# ---------------------------------------------------------------------------

class TestJsApiDiscover:
    def _make_session(self, bundle_js: str, base="http://example.com") -> MagicMock:
        sess = MagicMock(spec=requests.Session)
        html_resp = MagicMock()
        html_resp.text = '<script src="/main.js"></script>'
        html_resp.ok = True

        bundle_resp = MagicMock()
        bundle_resp.text = bundle_js
        bundle_resp.ok = True

        def _get(url, **kwargs):
            if url == base + "/":
                return html_resp
            return bundle_resp
        sess.get.side_effect = _get
        return sess

    def test_returns_list_of_three_tuples(self):
        sess = self._make_session('get(`${host}/rest/products`)')
        results = js_api_discover("http://example.com/", session=sess)
        assert isinstance(results, list)
        for item in results:
            assert len(item) == 3

    def test_method_is_uppercase(self):
        sess = self._make_session('post(`${host}/rest/users`, body)')
        results = js_api_discover("http://example.com/", session=sess)
        assert all(r[0] == r[0].upper() for r in results)

    def test_deduplication_across_passes(self):
        # Same URL from both method_templates and static_paths passes
        js = 'get(`${host}/rest/products`);\nvar x = "/rest/products";'
        sess = self._make_session(js)
        results = js_api_discover("http://example.com/", session=sess)
        urls = [r[1] for r in results]
        assert urls.count("http://example.com/rest/products") <= 1

    def test_creates_default_session_when_none_given(self):
        with patch("commonhuman_core.js_api_discover.requests.Session") as mock_cls:
            mock_sess = MagicMock()
            mock_sess.get.side_effect = OSError("no server")
            mock_cls.return_value = mock_sess
            results = js_api_discover("http://example.com/")
        mock_cls.assert_called_once()
        assert results == []

    def test_bundle_not_ok_is_skipped(self):
        sess = MagicMock(spec=requests.Session)
        html_resp = MagicMock()
        html_resp.text = '<script src="/main.js"></script>'
        bundle_resp = MagicMock()
        bundle_resp.ok = False
        bundle_resp.text = 'get(`${host}/rest/products`)'

        def _get(url, **kwargs):
            if "main.js" in url:
                return bundle_resp
            return html_resp
        sess.get.side_effect = _get
        assert js_api_discover("http://example.com/", session=sess) == []

    def test_bundle_oserror_is_skipped(self):
        sess = MagicMock(spec=requests.Session)
        html_resp = MagicMock()
        html_resp.text = '<script src="/main.js"></script>'

        def _get(url, **kwargs):
            if "main.js" in url:
                raise OSError("timeout")
            return html_resp
        sess.get.side_effect = _get
        assert js_api_discover("http://example.com/", session=sess) == []

    def test_results_from_all_three_extraction_passes(self):
        js = (
            'get(`${host}/rest/from-template`);\n'
            'var url = "/rest/from-static";\n'
            'dynBase = "/rest/indirect";\n'
            'get(dynBase + "/suffix")\n'
        )
        sess = self._make_session(js)
        results = js_api_discover("http://example.com/", session=sess)
        found = {r[1] for r in results}
        assert any("/rest/from-template" in u for u in found)
        assert any("/rest/from-static" in u for u in found)
        assert any("/rest/indirect/suffix" in u for u in found)

    def test_max_bundles_limits_fetched_bundles(self):
        # 30 script tags available; max_bundles=5 should cap total bundle fetches well below 30.
        # Each bundle is fetched twice (once in _collect_bundle_urls for chunk scanning,
        # once in the js_api_discover extraction loop), so the cap is max_bundles * 2.
        html = "".join(f'<script src="/bundle{i}.js"></script>' for i in range(30))
        fetch_count = [0]

        sess = MagicMock(spec=requests.Session)
        def _get(url, **kwargs):
            resp = MagicMock()
            resp.ok = True
            if url.endswith("/"):
                resp.text = html
            else:
                resp.text = ""
                fetch_count[0] += 1
            return resp
        sess.get.side_effect = _get

        js_api_discover("http://example.com/", session=sess, max_bundles=5)
        assert fetch_count[0] <= 10  # 5 bundles × 2 passes
