# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Unit tests for commonhuman_core.source_map — all I/O mocked."""

from __future__ import annotations

import base64
import json

import pytest

from commonhuman_core.source_map import (
    SourceMapResult,
    fetch_source_maps,
    _find_map_url,
    _fetch_map,
    _extract_sources,
    _is_noise,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_map(sources=None, sources_content=None, source_root=""):
    return {
        "version": 3,
        "sources": sources or [],
        "sourcesContent": sources_content or [],
        "sourceRoot": source_root,
        "mappings": "",
    }


def _inline_map(data: dict) -> str:
    raw  = json.dumps(data).encode()
    b64  = base64.b64encode(raw).decode()
    return f"data:application/json;base64,{b64}"


# ---------------------------------------------------------------------------
# SourceMapResult
# ---------------------------------------------------------------------------

class TestSourceMapResult:
    def test_initially_empty(self):
        r = SourceMapResult()
        assert r.sources == {}
        assert r.mapping == {}

    def test_len_reflects_sources(self):
        r = SourceMapResult()
        assert len(r) == 0
        r.sources["a.js"] = "code"
        assert len(r) == 1

    def test_all_sources_returns_values(self):
        r = SourceMapResult()
        r.sources["a.js"] = "aaa"
        r.sources["b.js"] = "bbb"
        vals = r.all_sources()
        assert "aaa" in vals and "bbb" in vals


# ---------------------------------------------------------------------------
# _is_noise
# ---------------------------------------------------------------------------

class TestIsNoise:
    @pytest.mark.parametrize("path", [
        "node_modules/lodash/merge.js",
        "webpack/runtime/chunk-loading.js",
        "src/utils.spec.js",
        "tests/helpers.test.ts",
        "/vendor/jquery.min.js",
    ])
    def test_noise_paths_detected(self, path):
        assert _is_noise(path) is True

    @pytest.mark.parametrize("path", [
        "src/components/App.jsx",
        "app/views/catalog.js",
        "lib/utils.js",
    ])
    def test_non_noise_paths_not_filtered(self, path):
        assert _is_noise(path) is False


# ---------------------------------------------------------------------------
# _find_map_url
# ---------------------------------------------------------------------------

class TestFindMapUrl:
    def test_finds_hash_comment(self):
        js = "var x=1;\n//# sourceMappingURL=bundle.js.map\n"
        url = _find_map_url(js, "https://cdn.example.com/bundle.js", "https://cdn.example.com")
        assert url == "https://cdn.example.com/bundle.js.map"

    def test_finds_at_comment(self):
        js = "//@ sourceMappingURL=bundle.js.map"
        url = _find_map_url(js, "https://cdn.example.com/bundle.js", "")
        assert url is not None and url.endswith("bundle.js.map")

    def test_absolute_url_returned_as_is(self):
        js = "//# sourceMappingURL=https://maps.example.com/app.js.map"
        url = _find_map_url(js, "https://app.example.com/app.js", "")
        assert url == "https://maps.example.com/app.js.map"

    def test_data_uri_returned_as_is(self):
        inline = "data:application/json;base64,abc=="
        js = f"//# sourceMappingURL={inline}"
        url = _find_map_url(js, "https://example.com/app.js", "")
        assert url == inline

    def test_no_comment_returns_none(self):
        assert _find_map_url("var x = 1;", "https://example.com/app.js", "") is None

    def test_searches_last_4096_chars(self):
        # Comment at the very end — should still be found
        padding = "a" * 5000
        js = padding + "\n//# sourceMappingURL=end.js.map"
        url = _find_map_url(js, "https://example.com/a.js", "")
        assert url is not None and "end.js.map" in url


# ---------------------------------------------------------------------------
# _fetch_map
# ---------------------------------------------------------------------------

class TestFetchMap:
    def test_fetches_remote_map(self):
        data = _make_map(["src/app.js"], ["console.log('hello');"])
        fetcher = lambda url: json.dumps(data)
        result = _fetch_map("https://example.com/bundle.js.map", "", fetcher)
        assert result is not None
        assert result["sources"] == ["src/app.js"]

    def test_returns_none_on_invalid_json(self):
        fetcher = lambda url: "not json"
        result = _fetch_map("https://example.com/map.js.map", "", fetcher)
        assert result is None

    def test_returns_none_when_fetcher_raises(self):
        def bad_fetcher(url):
            raise OSError("network error")
        result = _fetch_map("https://example.com/map.js.map", "", bad_fetcher)
        assert result is None

    def test_decodes_inline_data_uri(self):
        data = _make_map(["src/x.ts"], ["const x = 1;"])
        inline_url = _inline_map(data)
        result = _fetch_map(inline_url, "", lambda u: "")
        assert result is not None
        assert result["sources"] == ["src/x.ts"]

    def test_invalid_base64_in_data_uri_returns_none(self):
        result = _fetch_map("data:application/json;base64,NOTVALID!!!!", "", lambda u: "")
        assert result is None

    def test_returns_none_for_empty_response(self):
        result = _fetch_map("https://example.com/map.js.map", "", lambda u: "")
        assert result is None


# ---------------------------------------------------------------------------
# _extract_sources
# ---------------------------------------------------------------------------

class TestExtractSources:
    def test_extracts_source_content(self):
        r = SourceMapResult()
        data = _make_map(["src/app.js"], ["const a = 1;"])
        paths = _extract_sources("https://example.com/bundle.js", "https://example.com/bundle.js.map", data, r)
        assert "src/app.js" in paths
        assert r.sources.get("src/app.js") == "const a = 1;"

    def test_skips_noise_paths(self):
        r = SourceMapResult()
        data = _make_map(["node_modules/lib.js", "src/app.js"], ["noise", "code"])
        paths = _extract_sources("https://example.com/b.js", "", data, r)
        assert "node_modules/lib.js" not in paths
        assert "src/app.js" in paths

    def test_skips_missing_sources_content(self):
        r = SourceMapResult()
        data = _make_map(["src/app.js"], [])  # no content
        paths = _extract_sources("", "", data, r)
        assert paths == []

    def test_applies_source_root(self):
        r = SourceMapResult()
        data = _make_map(["app.js"], ["code"], source_root="/src")
        paths = _extract_sources("", "", data, r)
        assert "/src/app.js" in paths

    def test_no_duplicate_paths(self):
        r = SourceMapResult()
        data = _make_map(["src/app.js"], ["code"])
        _extract_sources("", "", data, r)
        _extract_sources("", "", data, r)  # second call same map
        assert list(r.sources.keys()).count("src/app.js") == 1


# ---------------------------------------------------------------------------
# fetch_source_maps (integration)
# ---------------------------------------------------------------------------

class TestFetchSourceMaps:
    def _make_fetcher(self, responses: dict):
        def fetcher(url):
            return responses.get(url, "")
        return fetcher

    def test_recovers_sources_from_bundle(self):
        map_data = _make_map(["src/app.js"], ["const x = 1;"])
        fetcher = self._make_fetcher({
            "https://cdn.example.com/bundle.js":
                "var x=1;\n//# sourceMappingURL=bundle.js.map",
            "https://cdn.example.com/bundle.js.map":
                json.dumps(map_data),
        })
        result = fetch_source_maps(
            ["https://cdn.example.com/bundle.js"],
            fetcher,
            base_url="https://cdn.example.com",
        )
        assert "src/app.js" in result.sources

    def test_skips_js_without_map_comment(self):
        fetcher = self._make_fetcher({
            "https://example.com/no-map.js": "var x = 1;",
        })
        result = fetch_source_maps(["https://example.com/no-map.js"], fetcher)
        assert result.sources == {}

    def test_respects_max_maps_limit(self):
        # Create 5 bundles each with a source map
        responses = {}
        js_urls = []
        for i in range(5):
            js_url = f"https://example.com/bundle{i}.js"
            map_url = f"https://example.com/bundle{i}.js.map"
            map_data = _make_map([f"src/file{i}.js"], [f"code{i}"])
            responses[js_url] = f"//# sourceMappingURL=bundle{i}.js.map"
            responses[map_url] = json.dumps(map_data)
            js_urls.append(js_url)

        result = fetch_source_maps(js_urls, self._make_fetcher(responses), max_maps=2)
        assert len(result.sources) == 2

    def test_handles_fetcher_exception_gracefully(self):
        def bad_fetcher(url):
            raise ConnectionError("timeout")
        result = fetch_source_maps(["https://example.com/app.js"], bad_fetcher)
        assert result.sources == {}

    def test_mapping_tracks_js_to_source_paths(self):
        map_data = _make_map(["src/a.js", "src/b.js"], ["aaa", "bbb"])
        js_url = "https://cdn.example.com/bundle.js"
        fetcher = self._make_fetcher({
            js_url: "//# sourceMappingURL=bundle.js.map",
            "https://cdn.example.com/bundle.js.map": json.dumps(map_data),
        })
        result = fetch_source_maps([js_url], fetcher)
        assert js_url in result.mapping
        assert "src/a.js" in result.mapping[js_url]
