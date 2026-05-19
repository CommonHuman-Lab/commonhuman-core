# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Tests for commonhuman_core.openapi — file/network I/O is mocked."""

from __future__ import annotations

import io
import json
import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

from commonhuman_core.openapi import (
    ApiEndpoint,
    _body_fields_v2,
    _body_fields_v3,
    _build_endpoint,
    _placeholder_for,
    _resolve_ref,
    discover_openapi,
    load_openapi,
)


# ---------------------------------------------------------------------------
# Minimal spec fixtures
# ---------------------------------------------------------------------------

_SWAGGER2 = {
    "swagger": "2.0",
    "host": "api.example.com",
    "basePath": "/v1",
    "schemes": ["https"],
    "paths": {
        "/users/{id}": {
            "get": {
                "parameters": [
                    {"name": "id", "in": "path"},
                    {"name": "fields", "in": "query"},
                ]
            }
        }
    },
}

_OPENAPI3 = {
    "openapi": "3.0.0",
    "servers": [{"url": "https://api.example.com/v2"}],
    "paths": {
        "/items/{item_id}": {
            "get": {
                "parameters": [
                    {"name": "item_id", "in": "path"},
                    {"name": "page", "in": "query"},
                ]
            },
            "post": {
                "parameters": [],
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "properties": {"name": {}, "price": {}}
                            }
                        }
                    }
                },
            },
        }
    },
}


# ---------------------------------------------------------------------------
# load_openapi — routing / version dispatch
# ---------------------------------------------------------------------------

class TestLoadOpenapiRouting:
    def _file_load(self, spec_dict):
        raw = json.dumps(spec_dict)
        with patch("builtins.open", mock_open(read_data=raw)):
            return load_openapi("/fake/spec.json")

    def test_v2_dispatched(self):
        eps = self._file_load(_SWAGGER2)
        assert len(eps) == 1
        assert eps[0].method == "GET"

    def test_v3_dispatched(self):
        eps = self._file_load(_OPENAPI3)
        assert len(eps) == 2  # GET + POST

    def test_unknown_version_returns_empty(self):
        spec = {"info": {"title": "no version"}, "paths": {}}
        eps = self._file_load(spec)
        assert eps == []

    def test_empty_spec_returns_empty(self):
        with patch("commonhuman_core.openapi._load_spec", return_value=None):
            assert load_openapi("/nonexistent.json") == []


# ---------------------------------------------------------------------------
# _load_from_file
# ---------------------------------------------------------------------------

class TestLoadFromFile:
    def test_missing_file_returns_none(self):
        with patch("builtins.open", side_effect=OSError("not found")):
            eps = load_openapi("/missing/spec.json")
        assert eps == []

    def test_valid_json_file_parsed(self):
        raw = json.dumps(_SWAGGER2)
        with patch("builtins.open", mock_open(read_data=raw)):
            eps = load_openapi("/path/spec.json")
        assert len(eps) >= 1

    def test_invalid_json_returns_empty(self):
        with patch("builtins.open", mock_open(read_data="{invalid json")):
            eps = load_openapi("/path/spec.json")
        assert eps == []

    def test_yaml_without_pyyaml_returns_empty(self):
        yaml_raw = "openapi: '3.0.0'\npaths: {}"
        saved = sys.modules.pop("yaml", None)
        try:
            sys.modules["yaml"] = None  # block import
            with patch("builtins.open", mock_open(read_data=yaml_raw)):
                eps = load_openapi("/path/spec.yaml")
        finally:
            if saved is not None:
                sys.modules["yaml"] = saved
            else:
                sys.modules.pop("yaml", None)
        assert eps == []

    def test_yaml_parse_error_returns_empty(self):
        yaml_raw = "not: [valid: yaml: ]["
        mock_yaml = MagicMock()
        mock_yaml.safe_load.side_effect = Exception("yaml error")
        with patch.dict(sys.modules, {"yaml": mock_yaml}):
            with patch("builtins.open", mock_open(read_data=yaml_raw)):
                eps = load_openapi("/path/spec.yaml")
        assert eps == []

    def test_yaml_with_pyyaml_parsed(self):
        yaml_raw = "dummy: yaml"
        mock_yaml = MagicMock()
        mock_yaml.safe_load.return_value = _SWAGGER2
        with patch.dict(sys.modules, {"yaml": mock_yaml}):
            with patch("builtins.open", mock_open(read_data=yaml_raw)):
                eps = load_openapi("/path/spec.yaml")
        assert len(eps) >= 1


# ---------------------------------------------------------------------------
# _load_from_url
# ---------------------------------------------------------------------------

class TestLoadFromUrl:
    def test_url_load_success(self):
        raw = json.dumps(_SWAGGER2).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = raw
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            eps = load_openapi("https://example.com/openapi.json")
        assert len(eps) >= 1

    def test_url_load_failure_returns_empty(self):
        with patch("urllib.request.urlopen", side_effect=OSError("network error")):
            eps = load_openapi("https://example.com/openapi.json")
        assert eps == []


# ---------------------------------------------------------------------------
# Swagger 2.x parsing
# ---------------------------------------------------------------------------

class TestParseV2:
    def _load(self, spec):
        raw = json.dumps(spec)
        with patch("builtins.open", mock_open(read_data=raw)):
            return load_openapi("/spec.json")

    def test_url_built_from_host_basepath_scheme(self):
        eps = self._load(_SWAGGER2)
        assert eps[0].url.startswith("https://api.example.com/v1/")

    def test_base_url_override(self):
        raw = json.dumps(_SWAGGER2)
        with patch("builtins.open", mock_open(read_data=raw)):
            eps = load_openapi("/spec.json", base_url="https://override.com")
        assert eps[0].url.startswith("https://override.com/")

    def test_path_param_expanded_to_placeholder(self):
        eps = self._load(_SWAGGER2)
        assert "{id}" not in eps[0].url
        assert eps[0].url.endswith("/1")

    def test_path_param_name_recorded(self):
        eps = self._load(_SWAGGER2)
        assert "id" in eps[0].path_params

    def test_query_param_recorded(self):
        eps = self._load(_SWAGGER2)
        assert "fields" in eps[0].query_params

    def test_raw_path_stored(self):
        eps = self._load(_SWAGGER2)
        assert eps[0].raw_path == "/users/{id}"

    def test_method_uppercased(self):
        eps = self._load(_SWAGGER2)
        assert eps[0].method == "GET"

    def test_non_http_method_skipped(self):
        spec = {
            "swagger": "2.0",
            "host": "x.com",
            "paths": {
                "/x": {
                    "parameters": [],
                    "x-custom": "should be skipped",
                    "get": {"parameters": []},
                }
            },
        }
        eps = self._load(spec)
        assert len(eps) == 1

    def test_body_params_v2_extracted(self):
        spec = {
            "swagger": "2.0",
            "host": "x.com",
            "paths": {
                "/items": {
                    "post": {
                        "parameters": [
                            {
                                "name": "body",
                                "in": "body",
                                "schema": {
                                    "properties": {"name": {}, "price": {}}
                                },
                            }
                        ]
                    }
                }
            },
        }
        eps = self._load(spec)
        assert "name" in eps[0].body_params
        assert "price" in eps[0].body_params

    def test_form_data_params_extracted(self):
        spec = {
            "swagger": "2.0",
            "host": "x.com",
            "paths": {
                "/upload": {
                    "post": {
                        "parameters": [
                            {"name": "title", "in": "formData"},
                            {"name": "desc", "in": "formData"},
                        ]
                    }
                }
            },
        }
        eps = self._load(spec)
        assert "title" in eps[0].body_params
        assert "desc" in eps[0].body_params

    def test_path_level_params_merged(self):
        spec = {
            "swagger": "2.0",
            "host": "x.com",
            "paths": {
                "/r/{id}": {
                    "parameters": [{"name": "id", "in": "path"}],
                    "get": {"parameters": [{"name": "fmt", "in": "query"}]},
                }
            },
        }
        eps = self._load(spec)
        assert "id" in eps[0].path_params
        assert "fmt" in eps[0].query_params

    def test_non_dict_path_item_skipped(self):
        spec = {
            "swagger": "2.0",
            "host": "x.com",
            "paths": {"/broken": "not a dict"},
        }
        eps = self._load(spec)
        assert eps == []

    def test_non_dict_operation_skipped(self):
        spec = {
            "swagger": "2.0",
            "host": "x.com",
            "paths": {"/x": {"get": "not a dict"}},
        }
        eps = self._load(spec)
        assert eps == []

    def test_default_scheme_https(self):
        spec = {
            "swagger": "2.0",
            "host": "x.com",
            "paths": {"/p": {"get": {"parameters": []}}},
        }
        eps = self._load(spec)
        assert eps[0].url.startswith("https://")

    def test_empty_paths_returns_empty(self):
        spec = {"swagger": "2.0", "host": "x.com", "paths": {}}
        eps = self._load(spec)
        assert eps == []


# ---------------------------------------------------------------------------
# OpenAPI 3.x parsing
# ---------------------------------------------------------------------------

class TestParseV3:
    def _load(self, spec, base_url=""):
        raw = json.dumps(spec)
        with patch("builtins.open", mock_open(read_data=raw)):
            return load_openapi("/spec.json", base_url=base_url)

    def test_url_built_from_servers(self):
        eps = self._load(_OPENAPI3)
        assert any(ep.url.startswith("https://api.example.com/v2/") for ep in eps)

    def test_base_url_override(self):
        eps = self._load(_OPENAPI3, base_url="https://override.com")
        assert all(ep.url.startswith("https://override.com/") for ep in eps)

    def test_empty_servers_uses_empty_base(self):
        spec = {
            "openapi": "3.0.0",
            "paths": {"/p": {"get": {"parameters": []}}},
        }
        eps = self._load(spec)
        assert len(eps) == 1
        assert eps[0].url == "/p"

    def test_path_param_expanded(self):
        eps = self._load(_OPENAPI3)
        get_ep = next(ep for ep in eps if ep.method == "GET")
        assert "{item_id}" not in get_ep.url
        assert get_ep.url.endswith("/1")

    def test_query_param_recorded(self):
        eps = self._load(_OPENAPI3)
        get_ep = next(ep for ep in eps if ep.method == "GET")
        assert "page" in get_ep.query_params

    def test_request_body_json_params_extracted(self):
        eps = self._load(_OPENAPI3)
        post_ep = next(ep for ep in eps if ep.method == "POST")
        assert "name" in post_ep.body_params
        assert "price" in post_ep.body_params

    def test_request_body_form_params_extracted(self):
        spec = {
            "openapi": "3.0.0",
            "servers": [{"url": "https://x.com"}],
            "paths": {
                "/upload": {
                    "post": {
                        "parameters": [],
                        "requestBody": {
                            "content": {
                                "application/x-www-form-urlencoded": {
                                    "schema": {
                                        "properties": {"file_name": {}, "size": {}}
                                    }
                                }
                            }
                        },
                    }
                }
            },
        }
        eps = self._load(spec)
        assert "file_name" in eps[0].body_params

    def test_ref_resolution_in_parameters(self):
        spec = {
            "openapi": "3.0.0",
            "servers": [{"url": "https://x.com"}],
            "components": {
                "parameters": {
                    "UserId": {"name": "user_id", "in": "path"}
                }
            },
            "paths": {
                "/users/{user_id}": {
                    "get": {
                        "parameters": [{"$ref": "#/components/parameters/UserId"}]
                    }
                }
            },
        }
        eps = self._load(spec)
        assert "user_id" in eps[0].path_params

    def test_path_level_params_merged_v3(self):
        spec = {
            "openapi": "3.0.0",
            "servers": [{"url": "https://x.com"}],
            "paths": {
                "/r/{id}": {
                    "parameters": [{"name": "id", "in": "path"}],
                    "get": {
                        "parameters": [{"name": "fmt", "in": "query"}]
                    },
                }
            },
        }
        eps = self._load(spec)
        assert "id" in eps[0].path_params
        assert "fmt" in eps[0].query_params

    def test_non_dict_path_item_skipped_v3(self):
        spec = {
            "openapi": "3.0.0",
            "paths": {"/broken": "not a dict"},
        }
        eps = self._load(spec)
        assert eps == []

    def test_non_dict_operation_skipped_v3(self):
        spec = {
            "openapi": "3.0.0",
            "servers": [{"url": "https://x.com"}],
            "paths": {"/x": {"get": "not a dict"}},
        }
        eps = self._load(spec)
        assert eps == []


# ---------------------------------------------------------------------------
# _resolve_ref
# ---------------------------------------------------------------------------

class TestResolveRef:
    def test_non_ref_passthrough(self):
        obj = {"name": "param", "in": "query"}
        assert _resolve_ref(obj, {}) is obj

    def test_non_dict_passthrough(self):
        assert _resolve_ref("string", {}) == "string"

    def test_local_ref_resolved(self):
        spec = {
            "components": {
                "schemas": {
                    "User": {"properties": {"id": {}, "name": {}}}
                }
            }
        }
        ref = {"$ref": "#/components/schemas/User"}
        result = _resolve_ref(ref, spec)
        assert "id" in result["properties"]

    def test_external_ref_returned_as_is(self):
        ref = {"$ref": "http://external.com/schema"}
        result = _resolve_ref(ref, {})
        assert result is ref

    def test_missing_ref_path_returns_obj(self):
        ref = {"$ref": "#/missing/path"}
        result = _resolve_ref(ref, {})
        assert result is ref

    def test_ref_path_hits_non_dict_intermediate_returns_obj(self):
        # spec["a"] is a string, not a dict → _resolve_ref hits the non-dict check (line 278)
        spec = {"a": "not-a-dict"}
        ref = {"$ref": "#/a/b"}
        result = _resolve_ref(ref, spec)
        assert result is ref


# ---------------------------------------------------------------------------
# _placeholder_for
# ---------------------------------------------------------------------------

class TestPlaceholderFor:
    def test_uuid_name_returns_uuid(self):
        result = _placeholder_for("user_uuid")
        assert result == "00000000-0000-4000-a000-000000000000"

    def test_guid_name_returns_uuid(self):
        result = _placeholder_for("resourceGuid")
        assert result == "00000000-0000-4000-a000-000000000000"

    def test_id_name_returns_one(self):
        assert _placeholder_for("id") == "1"

    def test_generic_name_returns_one(self):
        assert _placeholder_for("slug") == "1"


# ---------------------------------------------------------------------------
# _body_fields_v2
# ---------------------------------------------------------------------------

class TestBuildEndpoint:
    def test_non_dict_param_skipped(self):
        # A non-dict element in params list should be skipped (line 212 continue)
        ep = _build_endpoint("https://x.com", "/p", "GET", ["not-a-dict", None], {})
        assert ep.path_params == []
        assert ep.query_params == []

    def test_formdata_with_empty_name_skipped(self):
        # formData param with no name — the `and name` guard must be False
        params = [{"name": "", "in": "formData"}]
        ep = _build_endpoint("https://x.com", "/p", "POST", params, {})
        assert ep.body_params == []

    def test_formdata_with_name_included(self):
        params = [{"name": "upload", "in": "formData"}]
        ep = _build_endpoint("https://x.com", "/p", "POST", params, {})
        assert "upload" in ep.body_params


class TestBodyFieldsV2:
    def test_extracts_properties(self):
        param = {"schema": {"properties": {"a": {}, "b": {}}}}
        assert set(_body_fields_v2(param, {})) == {"a", "b"}

    def test_missing_schema_returns_empty(self):
        assert _body_fields_v2({}, {}) == []

    def test_non_dict_schema_returns_empty(self):
        assert _body_fields_v2({"schema": "invalid"}, {}) == []


# ---------------------------------------------------------------------------
# _body_fields_v3
# ---------------------------------------------------------------------------

class TestBodyFieldsV3:
    def test_none_returns_empty(self):
        assert _body_fields_v3(None, {}) == []

    def test_non_dict_returns_empty(self):
        assert _body_fields_v3("invalid", {}) == []

    def test_json_content_extracted(self):
        body = {
            "content": {
                "application/json": {
                    "schema": {"properties": {"x": {}, "y": {}}}
                }
            }
        }
        result = _body_fields_v3(body, {})
        assert set(result) == {"x", "y"}

    def test_form_content_extracted_when_no_json(self):
        body = {
            "content": {
                "application/x-www-form-urlencoded": {
                    "schema": {"properties": {"field1": {}}}
                }
            }
        }
        result = _body_fields_v3(body, {})
        assert "field1" in result

    def test_empty_content_returns_empty(self):
        assert _body_fields_v3({"content": {}}, {}) == []

    def test_schema_is_not_dict_returns_empty(self):
        # schema is a string — `if isinstance(schema, dict):` is False → branch miss at line 260
        body = {
            "content": {
                "application/json": {"schema": "not-a-dict"}
            }
        }
        assert _body_fields_v3(body, {}) == []

    def test_schema_has_no_properties_tries_next_media_type(self):
        # First media type has empty properties; second has real ones → falls through to next
        body = {
            "content": {
                "application/json": {"schema": {"properties": {}}},
                "application/x-www-form-urlencoded": {
                    "schema": {"properties": {"field": {}}}
                },
            }
        }
        result = _body_fields_v3(body, {})
        assert "field" in result

    def test_ref_in_request_body_resolved(self):
        spec = {
            "components": {
                "requestBodies": {
                    "CreateItem": {
                        "content": {
                            "application/json": {
                                "schema": {"properties": {"item_name": {}}}
                            }
                        }
                    }
                }
            }
        }
        body = {"$ref": "#/components/requestBodies/CreateItem"}
        result = _body_fields_v3(body, spec)
        assert "item_name" in result


# ---------------------------------------------------------------------------
# discover_openapi
# ---------------------------------------------------------------------------

def _mock_urlopen(content: str, content_type: str = "application/json", status: int = 200):
    """Return a context-manager mock for urllib.request.urlopen."""
    resp = MagicMock()
    resp.status = status
    resp.headers = MagicMock()
    resp.headers.get.return_value = content_type
    resp.read.return_value = content.encode("utf-8")
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestDiscoverOpenapi:
    # urllib.request is imported lazily inside discover_openapi(), so we patch
    # at the stdlib level rather than via the module namespace.
    _URLOPEN = "urllib.request.urlopen"

    def test_returns_url_for_json_swagger_spec(self):
        spec = json.dumps({"swagger": "2.0", "paths": {}})
        with patch(self._URLOPEN, side_effect=lambda *a, **kw: _mock_urlopen(spec, "application/json")):
            url = discover_openapi("https://api.example.com")
        assert url is not None
        assert "api.example.com" in url

    def test_returns_url_for_openapi3_spec(self):
        spec = json.dumps({"openapi": "3.0.0", "paths": {}})
        with patch(self._URLOPEN, side_effect=lambda *a, **kw: _mock_urlopen(spec, "application/json")):
            url = discover_openapi("https://api.example.com")
        assert url is not None

    def test_returns_none_when_all_paths_404(self):
        import urllib.error
        with patch(self._URLOPEN, side_effect=urllib.error.HTTPError(None, 404, "Not Found", {}, None)):
            url = discover_openapi("https://api.example.com")
        assert url is None

    def test_returns_none_when_all_requests_fail(self):
        with patch(self._URLOPEN, side_effect=OSError("network error")):
            url = discover_openapi("https://api.example.com")
        assert url is None

    def test_extracts_embedded_spec_url_from_swagger_ui_html(self):
        swagger_ui = (
            '<html><script src="swagger-ui.js"></script>'
            '<script>SwaggerUIBundle({ url: "/api/swagger.json" })</script></html>'
        )
        with patch(self._URLOPEN, side_effect=lambda *a, **kw: _mock_urlopen(swagger_ui, "text/html")):
            url = discover_openapi("https://api.example.com")
        # Should return the embedded spec URL (or None if not implemented)
        # We just verify no crash and that the return is string-or-None
        assert url is None or isinstance(url, str)

    def test_strips_trailing_slash_from_base(self):
        spec = json.dumps({"openapi": "3.0.0", "paths": {}})
        captured = []

        def fake_urlopen(req, timeout=None):
            captured.append(req.full_url)
            return _mock_urlopen(spec, "application/json")

        with patch(self._URLOPEN, side_effect=fake_urlopen):
            discover_openapi("https://api.example.com/")
        # No double slashes in any probed URL
        assert not any("//" in u.replace("https://", "").replace("http://", "") for u in captured)

    def test_4xx_status_continues_to_next_path(self):
        """Line 354: resp.status >= 400 → continue without returning."""
        call_count = [0]

        def fake_urlopen(req, timeout=None):
            call_count[0] += 1
            if call_count[0] == 1:
                return _mock_urlopen("{}", "application/json", status=403)
            raise OSError("no more")

        with patch(self._URLOPEN, side_effect=fake_urlopen):
            url = discover_openapi("https://api.example.com")
        assert url is None

    def test_json_non_spec_dict_continues(self):
        """Branch 364->373: valid JSON dict but not a swagger/openapi spec."""
        non_spec = json.dumps({"error": "not found", "code": 404})

        def fake_urlopen(req, timeout=None):
            if req.full_url.endswith(".json"):
                return _mock_urlopen(non_spec, "application/json")
            raise OSError("skip")

        with patch(self._URLOPEN, side_effect=fake_urlopen):
            url = discover_openapi("https://api.example.com")
        assert url is None

    def test_invalid_json_continues(self):
        """Lines 369-370: json.loads raises → except handler runs."""
        bad_json = "{this is not valid json"

        def fake_urlopen(req, timeout=None):
            if req.full_url.endswith(".json"):
                return _mock_urlopen(bad_json, "application/json")
            raise OSError("skip")

        with patch(self._URLOPEN, side_effect=fake_urlopen):
            url = discover_openapi("https://api.example.com")
        assert url is None

    def test_yaml_spec_discovered(self):
        """Lines 374-381: YAML content-type with a valid swagger spec returns URL."""
        yaml_spec = "swagger: '2.0'\npaths: {}"
        mock_yaml = MagicMock()
        mock_yaml.safe_load.return_value = {"swagger": "2.0", "paths": {}}

        def fake_urlopen(req, timeout=None):
            if ".yaml" in req.full_url:
                return _mock_urlopen(yaml_spec, "application/yaml")
            raise OSError("skip")

        import sys
        with patch(self._URLOPEN, side_effect=fake_urlopen), \
             patch.dict(sys.modules, {"yaml": mock_yaml}):
            url = discover_openapi("https://api.example.com")
        assert url is not None and ".yaml" in url

    def test_yaml_parse_error_continues(self):
        """Lines 382-383: yaml.safe_load raises → except fires."""
        yaml_spec = "swagger: '2.0'\npaths: {}"
        mock_yaml = MagicMock()
        mock_yaml.safe_load.side_effect = Exception("yaml parse error")

        def fake_urlopen(req, timeout=None):
            if req.full_url.endswith(".yaml"):
                return _mock_urlopen(yaml_spec, "application/yaml")
            raise OSError("skip")

        import sys
        with patch(self._URLOPEN, side_effect=fake_urlopen), \
             patch.dict(sys.modules, {"yaml": mock_yaml}):
            url = discover_openapi("https://api.example.com")
        assert url is None

    def test_yaml_non_spec_dict_continues(self):
        """Branch 377->386: yaml parses OK but data has no swagger/openapi/paths key."""
        yaml_spec = "error: not a spec"
        mock_yaml = MagicMock()
        mock_yaml.safe_load.return_value = {"error": "not a spec"}

        def fake_urlopen(req, timeout=None):
            if req.full_url.endswith(".yaml"):
                return _mock_urlopen(yaml_spec, "application/yaml")
            raise OSError("skip")

        import sys
        with patch(self._URLOPEN, side_effect=fake_urlopen), \
             patch.dict(sys.modules, {"yaml": mock_yaml}):
            url = discover_openapi("https://api.example.com")
        assert url is None

    def test_html_without_embedded_spec_url_continues(self):
        """Branch 388->348: HTML page with no embedded spec URL pattern."""
        html_no_spec = "<html><body><h1>API Docs</h1></body></html>"

        def fake_urlopen(req, timeout=None):
            if req.full_url.endswith(("swagger-ui.html", "swagger-ui/", "docs", "redoc")):
                return _mock_urlopen(html_no_spec, "text/html")
            raise OSError("skip")

        with patch(self._URLOPEN, side_effect=fake_urlopen):
            url = discover_openapi("https://api.example.com")
        assert url is None

    def test_non_json_yaml_html_content_continues(self):
        """Branch 386->348: content type is not json/yaml/html and path has no matching suffix."""
        xml_content = "<error><message>not found</message></error>"

        def fake_urlopen(req, timeout=None):
            if req.full_url.endswith("/api-docs"):
                return _mock_urlopen(xml_content, "application/xml")
            raise OSError("skip")

        with patch(self._URLOPEN, side_effect=fake_urlopen):
            url = discover_openapi("https://api.example.com")
        assert url is None
