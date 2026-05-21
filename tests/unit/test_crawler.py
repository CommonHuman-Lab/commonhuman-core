# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Tests for commonhuman_core.crawler."""

from __future__ import annotations

from unittest.mock import MagicMock

from commonhuman_core.crawler import (
    CrawlResult,
    _extract_forms,
    _extract_links,
    _normalise,
    crawl,
)
from commonhuman_core.http import HttpClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _html_resp(text: str, url: str = "https://example.com/") -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.text        = text
    r.headers     = {"content-type": "text/html"}
    r.url         = url
    return r


def _injector_returning(responses: list) -> HttpClient:
    """Return a mock HttpClient whose get() pops from responses."""
    inj = MagicMock(spec=HttpClient)
    inj.get.side_effect    = responses
    inj.get_params.side_effect = lambda url: []
    inj.same_origin.return_value = True
    return inj


# ---------------------------------------------------------------------------
# _normalise
# ---------------------------------------------------------------------------


class TestNormalise:
    def test_strips_trailing_slash(self):
        assert _normalise("https://example.com/path/") == "https://example.com/path"

    def test_strips_fragment(self):
        assert _normalise("https://example.com/#section") == "https://example.com/"

    def test_lowercases_scheme_and_host(self):
        assert _normalise("HTTPS://EXAMPLE.COM/") == "https://example.com/"

    def test_preserves_query(self):
        n = _normalise("https://example.com/path?a=1")
        assert "a=1" in n

    def test_root_path_normalised(self):
        assert _normalise("https://example.com") == "https://example.com/"


# ---------------------------------------------------------------------------
# _extract_links
# ---------------------------------------------------------------------------


class TestExtractLinks:
    def test_finds_anchor_hrefs(self):
        html = '<a href="/page1">one</a><a href="/page2">two</a>'
        links = _extract_links(html, "https://example.com/")
        assert any("page1" in l for l in links)
        assert any("page2" in l for l in links)

    def test_skips_javascript_hrefs(self):
        html = '<a href="javascript:void(0)">x</a>'
        assert _extract_links(html, "https://example.com/") == []

    def test_skips_mailto(self):
        html = '<a href="mailto:a@b.com">x</a>'
        assert _extract_links(html, "https://example.com/") == []

    def test_skips_fragment_only(self):
        html = '<a href="#section">x</a>'
        assert _extract_links(html, "https://example.com/") == []

    def test_resolves_relative_urls(self):
        links = _extract_links('<a href="sub/page">x</a>', "https://example.com/base/")
        assert any("example.com" in l for l in links)

    def test_strips_fragment_from_absolute(self):
        links = _extract_links('<a href="https://example.com/p#sec">x</a>', "https://example.com/")
        assert all("#" not in l for l in links)

    def test_button_formaction_extracted(self):
        """Lines 224-226: <button formaction> adds a link."""
        html = '<button formaction="/submit-form">Submit</button>'
        links = _extract_links(html, "https://example.com/")
        assert any("submit-form" in l for l in links)

    def test_data_href_extracted(self):
        """Line 232: data-href attribute adds a link."""
        html = '<div data-href="/page-via-data-attr">Click</div>'
        links = _extract_links(html, "https://example.com/")
        assert any("page-via-data-attr" in l for l in links)

    def test_code_tag_path_extracted(self):
        """Lines 238-245: text inside <code> matching path regex is extracted."""
        html = '<code>/api/items/1</code>'
        links = _extract_links(html, "https://example.com/")
        assert any("api/items" in l for l in links)

    def test_code_tag_colon_param_extracted(self):
        """:param style paths in <code> blocks must be extracted (regression for 01c5f65)."""
        html = '<code>/rest/basket/:bid</code>'
        links = _extract_links(html, "https://example.com/")
        assert any("rest/basket" in l for l in links)

    def test_code_tag_brace_param_extracted(self):
        """{param} style paths in <code> blocks must be extracted (regression for 01c5f65)."""
        html = '<code>/api/Products/{id}</code>'
        links = _extract_links(html, "https://example.com/")
        assert any("api/Products" in l for l in links)

    def test_code_tag_non_path_text_not_extracted(self):
        """Branch 244->exit: code text that doesn't match _CODE_PATH_RE is ignored."""
        html = '<code>some plain descriptive text</code>'
        links = _extract_links(html, "https://example.com/")
        assert links == []

    def test_button_without_formaction_does_not_add_link(self):
        """Branch 225->229: button with empty formaction skips _add."""
        html = '<button>No action button</button>'
        links = _extract_links(html, "https://example.com/")
        assert links == []

    def test_button_with_javascript_formaction_skipped(self):
        """Branch 225->229: button formaction starting with javascript: is skipped."""
        html = '<button formaction="javascript:void(0)">Bad</button>'
        links = _extract_links(html, "https://example.com/")
        assert links == []


# ---------------------------------------------------------------------------
# _extract_forms
# ---------------------------------------------------------------------------


class TestExtractForms:
    def test_extracts_form_action_and_method(self):
        html = '<form action="/login" method="POST"><input name="user"></form>'
        forms = _extract_forms(html, "https://example.com/")
        assert len(forms) == 1
        assert forms[0].method == "POST"
        assert "login" in forms[0].action

    def test_default_method_is_get(self):
        html = '<form><input name="q"></form>'
        forms = _extract_forms(html, "https://example.com/")
        assert forms[0].method == "GET"

    def test_hidden_inputs_go_to_base_data(self):
        html = '<form><input type="hidden" name="csrf" value="tok"><input name="q"></form>'
        forms = _extract_forms(html, "https://example.com/")
        assert "csrf" in forms[0].base_data
        assert "csrf" not in forms[0].params

    def test_submit_button_goes_to_base_data(self):
        html = '<form><input name="q"><input type="submit" name="sub" value="Go"></form>'
        forms = _extract_forms(html, "https://example.com/")
        assert "sub" in forms[0].base_data
        assert "sub" not in forms[0].params

    def test_textarea_is_injectable(self):
        html = '<form><textarea name="body"></textarea></form>'
        forms = _extract_forms(html, "https://example.com/")
        assert "body" in forms[0].params

    def test_select_is_injectable(self):
        html = '<form><select name="opt"><option>a</option></select></form>'
        forms = _extract_forms(html, "https://example.com/")
        assert "opt" in forms[0].params

    def test_button_and_image_skipped(self):
        html = '<form><input name="q"><input type="button" name="b"><input type="image" name="i"></form>'
        forms = _extract_forms(html, "https://example.com/")
        assert "b" not in forms[0].params
        assert "i" not in forms[0].params

    def test_form_without_inputs_not_collected(self):
        html = '<form action="/x"></form>'
        assert _extract_forms(html, "https://example.com/") == []

    def test_empty_html_returns_empty(self):
        assert _extract_forms("", "https://example.com/") == []

    def test_nameless_textarea_not_injectable(self):
        html = '<form><textarea></textarea><input name="q"></form>'
        forms = _extract_forms(html, "https://example.com/")
        assert len(forms) == 1
        assert list(forms[0].params.keys()) == ["q"]


# ---------------------------------------------------------------------------
# crawl()
# ---------------------------------------------------------------------------


class TestCrawl:
    def _make_injector(self, responses: list) -> HttpClient:
        inj = MagicMock(spec=HttpClient)
        inj.get.side_effect      = responses
        inj.get_params.side_effect = lambda url: []
        inj.same_origin.return_value = True
        return inj

    def test_returns_crawl_result(self):
        inj = self._make_injector([_html_resp("<html></html>")])
        result = crawl("https://example.com/", inj, max_pages=1)
        assert isinstance(result, CrawlResult)

    def test_visits_start_url(self):
        inj = self._make_injector([_html_resp("<html></html>")])
        result = crawl("https://example.com/", inj, max_pages=1)
        assert len(result.visited_urls) == 1

    def test_page_source_stored(self):
        html = "<html><body>hello</body></html>"
        inj  = self._make_injector([_html_resp(html)])
        result = crawl("https://example.com/", inj, max_pages=1)
        assert any("hello" in v for v in result.page_sources.values())

    def test_max_pages_respected(self):
        links_page = _html_resp(
            '<html><a href="/a">a</a><a href="/b">b</a><a href="/c">c</a></html>'
        )
        sub_page = _html_resp("<html></html>", url="https://example.com/sub")
        inj = self._make_injector([links_page] + [sub_page] * 10)
        result = crawl("https://example.com/", inj, max_pages=1)
        assert len(result.visited_urls) <= 1

    def test_exclude_patterns_skip_urls(self):
        links_page = _html_resp(
            '<html><a href="/admin">admin</a><a href="/page">page</a></html>'
        )
        sub_page = _html_resp("<html></html>", url="https://example.com/page")
        inj = self._make_injector([links_page, sub_page])
        result = crawl(
            "https://example.com/", inj,
            max_pages=10,
            exclude_patterns=[r"/admin"],
        )
        assert not any("admin" in u for u in result.visited_urls)

    def test_same_origin_false_allows_external(self):
        inj = self._make_injector([_html_resp("<html></html>")])
        inj.same_origin.return_value = False
        # With same_origin=False, no URLs skipped for origin reasons
        result = crawl("https://example.com/", inj, max_pages=1, same_origin=False)
        assert len(result.visited_urls) >= 1

    def test_forms_collected(self):
        html = '<html><form action="/s" method="POST"><input name="q"></form></html>'
        inj  = self._make_injector([_html_resp(html)])
        result = crawl("https://example.com/", inj, max_pages=1)
        assert len(result.form_targets) == 1
        assert result.form_targets[0].method == "POST"

    def test_url_params_collected(self):
        inj = self._make_injector([_html_resp("<html></html>")])
        inj.get_params.side_effect = lambda url: ["q", "page"]
        result = crawl("https://example.com/?q=1&page=2", inj, max_pages=1)
        assert len(result.url_params) == 1
        assert "q" in result.url_params[0][1]

    def test_fetch_exception_skipped(self):
        inj = MagicMock(spec=HttpClient)
        inj.get.side_effect      = Exception("connection refused")
        inj.get_params.side_effect = lambda url: []
        inj.same_origin.return_value = True
        result = crawl("https://example.com/", inj, max_pages=1)
        assert isinstance(result, CrawlResult)

    def test_4xx_response_not_stored(self):
        r = MagicMock()
        r.status_code = 404
        r.headers     = {"content-type": "text/html"}
        r.url         = "https://example.com/"
        inj = self._make_injector([r])
        result = crawl("https://example.com/", inj, max_pages=1)
        assert result.visited_urls == []

    def test_deduplication_no_revisit(self):
        html = '<html><a href="/">home</a></html>'
        inj  = self._make_injector([_html_resp(html)] * 5)
        result = crawl("https://example.com/", inj, max_pages=10)
        assert len(set(result.visited_urls)) == len(result.visited_urls)

    def test_non_html_response_not_in_visited_urls(self):
        r = MagicMock()
        r.status_code = 200
        r.headers     = {"content-type": "application/json"}
        r.url         = "https://example.com/"
        inj = self._make_injector([r])
        result = crawl("https://example.com/", inj, max_pages=1)
        assert result.visited_urls == []

    def test_non_html_url_params_still_extracted(self):
        """JSON-returning endpoints must still surface URL query params for injection."""
        r = MagicMock()
        r.status_code = 200
        r.headers     = {"content-type": "application/json"}
        r.url         = "https://example.com/"
        inj = self._make_injector([r])
        inj.get_params.side_effect = lambda url: ["id"] if "id=" in url else []
        result = crawl("https://example.com/?id=1", inj, max_pages=1)
        assert result.visited_urls == []
        assert len(result.url_params) == 1
        assert "id" in result.url_params[0][1]

    def test_off_origin_url_filtered(self):
        html = '<html><a href="https://evil.com/path">evil</a></html>'
        inj  = self._make_injector([_html_resp(html)])
        inj.same_origin.side_effect = lambda a, b: "example.com" in a
        result = crawl("https://example.com/", inj, max_pages=10, same_origin=True)
        assert not any("evil.com" in u for u in result.visited_urls)

    def test_excluded_start_url_returns_empty(self):
        inj = self._make_injector([])
        result = crawl(
            "https://example.com/admin", inj,
            max_pages=10,
            exclude_patterns=[r"/admin"],
        )
        assert result.visited_urls == []

    def test_depth_limit_links_not_followed(self):
        start_html = '<html><a href="/sub">sub</a></html>'
        sub_html   = _html_resp(
            '<html><a href="/deep">deep</a></html>',
            url="https://example.com/sub",
        )
        inj = self._make_injector([_html_resp(start_html), sub_html])
        result = crawl("https://example.com/", inj, max_pages=10, max_depth=1)
        assert not any("deep" in u for u in result.visited_urls)

    def test_future_exception_skips_url(self):
        r = MagicMock()
        r.status_code = 200
        r.headers.get.side_effect = RuntimeError("headers broken")
        inj = MagicMock(spec=HttpClient)
        inj.get.return_value      = r
        inj.get_params.side_effect = lambda url: []
        inj.same_origin.return_value = True
        result = crawl("https://example.com/", inj, max_pages=1)
        assert isinstance(result, CrawlResult)
        assert result.visited_urls == []

    def test_path_param_candidate_detected_from_json_url(self):
        """URLs with numeric path segments that return JSON are added to path_param_candidates."""
        r = MagicMock()
        r.status_code = 200
        r.headers     = {"content-type": "application/json"}
        r.url         = "https://example.com/api/items/1"
        index = _html_resp('<html><a href="/api/items/1">item</a></html>')
        inj   = self._make_injector([index, r])
        result = crawl("https://example.com/", inj, max_pages=5, max_depth=1)
        assert "https://example.com/api/items/1" in result.path_param_candidates

    def test_code_tag_paths_followed_as_links(self):
        """URL-like strings inside <code> tags are queued as links to visit."""
        api_resp = MagicMock()
        api_resp.status_code = 200
        api_resp.headers     = {"content-type": "application/json"}
        api_resp.url         = "https://example.com/api/v1/users/42"
        index = _html_resp('<html><code>/api/v1/users/42</code></html>')
        inj   = self._make_injector([index, api_resp])
        result = crawl("https://example.com/", inj, max_pages=5, max_depth=1)
        assert "https://example.com/api/v1/users/42" in result.path_param_candidates

    def test_non_numeric_path_not_a_candidate(self):
        """Paths without numeric segments are not added to path_param_candidates."""
        r = MagicMock()
        r.status_code = 200
        r.headers     = {"content-type": "application/json"}
        r.url         = "http://example.com/api/status"
        index = _html_resp('<a href="/api/status">s</a>')
        inj   = self._make_injector([_html_resp('<html><a href="/api/status">s</a></html>'), r])
        result = crawl("http://example.com/", inj, max_pages=5, max_depth=1)
        assert "http://example.com/api/status" not in result.path_param_candidates

    def test_duplicate_url_in_batch_deduplicated(self):
        start_html = '<html><a href="/a">a</a><a href="/b">b</a></html>'
        ab_html    = _html_resp(
            '<html><a href="/c">c</a></html>', url="https://example.com/ab"
        )
        page_c = _html_resp("<html></html>", url="https://example.com/c")
        inj = self._make_injector(
            [_html_resp(start_html), ab_html, ab_html, page_c]
        )
        result = crawl("https://example.com/", inj, max_pages=10, threads=1)
        c_urls = [u for u in result.visited_urls if "/c" in u]
        assert len(c_urls) == 1


# ---------------------------------------------------------------------------
# Form action enqueueing branches
# ---------------------------------------------------------------------------


class TestFormActionEnqueue:
    def _make_injector(self, responses: list) -> HttpClient:
        inj = MagicMock(spec=HttpClient)
        inj.get.side_effect        = responses
        inj.get_params.side_effect = lambda url: []
        inj.same_origin.return_value = True
        return inj

    def test_form_action_not_enqueued_at_max_depth(self):
        """Branch 149->145: depth == max_depth so form action is not queued."""
        html = '<html><form action="/action" method="POST"><input name="q"></form></html>'
        inj  = self._make_injector([_html_resp(html)])
        result = crawl("https://example.com/", inj, max_pages=10, max_depth=0)
        assert len(result.form_targets) == 1

    def test_form_action_already_visited_not_enqueued(self):
        """Branch 151->145: form action normalises to start URL (already visited)."""
        html = '<html><form action="/" method="POST"><input name="q"></form></html>'
        inj  = self._make_injector([_html_resp(html)])
        result = crawl("https://example.com/", inj, max_pages=10, max_depth=1)
        assert len(result.form_targets) == 1

    def test_off_origin_form_action_not_enqueued(self):
        """Branch 152->145: same_origin=True and form action is off-origin."""
        html = '<html><form action="https://evil.com/steal" method="POST"><input name="q"></form></html>'
        inj  = self._make_injector([_html_resp(html)])
        inj.same_origin.side_effect = lambda a, b: "example.com" in a
        result = crawl("https://example.com/", inj, max_pages=10, max_depth=1, same_origin=True)
        assert len(result.form_targets) == 1
