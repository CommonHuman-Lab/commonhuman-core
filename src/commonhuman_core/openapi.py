# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""
commonhuman-core — openapi.py
OpenAPI 2.x (Swagger) and 3.x spec import.

Produces ApiEndpoint objects suitable for feeding into any scanner's target list.
Path parameters are expanded with type-appropriate placeholder values so the
scanner can detect and mutate them immediately.

Supports JSON specs natively.  YAML specs require pyyaml:
    pip install 'commonhuman-core[openapi]'
"""
from __future__ import annotations

import json
import logging
import re
import urllib.parse as up
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public data type
# ---------------------------------------------------------------------------

@dataclass
class ApiEndpoint:
    """One HTTP operation from an OpenAPI spec, ready to scan."""
    url:          str
    method:       str
    path_params:  List[str] = field(default_factory=list)
    query_params: List[str] = field(default_factory=list)
    body_params:  List[str] = field(default_factory=list)
    raw_path:     str = ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_openapi(source: str, base_url: str = "") -> List[ApiEndpoint]:
    """Load an OpenAPI 2.x or 3.x spec from a file path or URL.

    Returns a list of ApiEndpoints with path parameters replaced by
    type-appropriate placeholder values (e.g. ``{id}`` → ``1``,
    ``{uuid}`` → ``00000000-0000-4000-a000-000000000000``).

    ``base_url`` overrides the server URL declared in the spec.
    """
    spec = _load_spec(source)
    if not spec:
        return []

    version = str(spec.get("swagger", "") or spec.get("openapi", ""))
    if version.startswith("2"):
        return _parse_v2(spec, base_url)
    if version.startswith("3"):
        return _parse_v3(spec, base_url)

    logger.warning("openapi: unrecognised spec version %r in %s", version, source)
    return []


# ---------------------------------------------------------------------------
# Spec loaders
# ---------------------------------------------------------------------------

def _load_spec(source: str) -> Optional[Dict[str, Any]]:
    if source.startswith(("http://", "https://")):
        return _load_from_url(source)
    return _load_from_file(source)


def _load_from_file(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        logger.error("openapi: cannot read spec %r: %s", path, exc)
        return None
    return _parse_raw(raw, path)


def _load_from_url(url: str) -> Optional[Dict[str, Any]]:
    try:
        import urllib.request
        with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
            raw = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.error("openapi: cannot fetch spec %r: %s", url, exc)
        return None
    return _parse_raw(raw, url)


def _parse_raw(raw: str, source: str) -> Optional[Dict[str, Any]]:
    stripped = raw.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error("openapi: JSON parse error in %r: %s", source, exc)
            return None
    try:
        import yaml  # type: ignore[import-untyped]
        return yaml.safe_load(raw)
    except ImportError:
        logger.error(
            "openapi: YAML spec detected but pyyaml is not installed — "
            "pip install 'commonhuman-core[openapi]'"
        )
    except Exception as exc:
        logger.error("openapi: YAML parse error in %r: %s", source, exc)
    return None


# ---------------------------------------------------------------------------
# OpenAPI 2.x (Swagger) parser
# ---------------------------------------------------------------------------

def _parse_v2(spec: Dict[str, Any], base_url_override: str) -> List[ApiEndpoint]:
    if base_url_override:
        base = base_url_override.rstrip("/")
    else:
        schemes   = spec.get("schemes") or ["https"]
        host      = spec.get("host", "localhost")
        base_path = (spec.get("basePath") or "").rstrip("/")
        base      = f"{schemes[0]}://{host}{base_path}"

    endpoints: List[ApiEndpoint] = []
    for raw_path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        path_level_params = list(path_item.get("parameters") or [])
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                continue
            params = path_level_params + list(operation.get("parameters") or [])
            endpoints.append(_build_endpoint(base, raw_path, method.upper(), params, spec))

    logger.info("openapi: loaded %d endpoint(s) from Swagger 2.x", len(endpoints))
    return endpoints


# ---------------------------------------------------------------------------
# OpenAPI 3.x parser
# ---------------------------------------------------------------------------

def _parse_v3(spec: Dict[str, Any], base_url_override: str) -> List[ApiEndpoint]:
    if base_url_override:
        base = base_url_override.rstrip("/")
    else:
        servers = spec.get("servers") or []
        base    = (servers[0].get("url") if servers else "").rstrip("/")

    endpoints: List[ApiEndpoint] = []
    for raw_path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        path_level_params = [
            _resolve_ref(p, spec)
            for p in (path_item.get("parameters") or [])
            if isinstance(p, dict)
        ]
        for method, operation in path_item.items():
            if method.lower() not in _HTTP_METHODS:
                continue
            if not isinstance(operation, dict):
                continue
            op_params = [
                _resolve_ref(p, spec)
                for p in (operation.get("parameters") or [])
                if isinstance(p, dict)
            ]
            params = path_level_params + op_params
            endpoints.append(
                _build_endpoint(base, raw_path, method.upper(), params, spec,
                                v3=True, operation=operation)
            )

    logger.info("openapi: loaded %d endpoint(s) from OpenAPI 3.x", len(endpoints))
    return endpoints


# ---------------------------------------------------------------------------
# Shared endpoint builder
# ---------------------------------------------------------------------------

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head"})


def _build_endpoint(
    base:      str,
    raw_path:  str,
    method:    str,
    params:    List[Dict],
    spec:      Dict,
    v3:        bool = False,
    operation: Optional[Dict] = None,
) -> ApiEndpoint:
    path_params:  List[str] = []
    query_params: List[str] = []
    body_params:  List[str] = []

    for p in params:
        if not isinstance(p, dict):
            continue
        name  = p.get("name", "")
        where = p.get("in", "")
        if where == "path":
            path_params.append(name)
        elif where == "query":
            query_params.append(name)
        elif where == "body":
            body_params.extend(_body_fields_v2(p, spec))
        elif where == "formData" and name:
            body_params.append(name)

    if v3 and operation:
        body_params.extend(_body_fields_v3(operation.get("requestBody"), spec))

    # Replace path-parameter placeholders with example values
    expanded = raw_path
    for pname in path_params:
        expanded = expanded.replace("{" + pname + "}", _placeholder_for(pname))

    return ApiEndpoint(
        url=base + expanded,
        method=method,
        path_params=path_params,
        query_params=query_params,
        body_params=body_params,
        raw_path=raw_path,
    )


def _body_fields_v2(param: Dict, spec: Dict) -> List[str]:
    schema = _resolve_ref(param.get("schema") or {}, spec)
    props  = schema.get("properties") or {} if isinstance(schema, dict) else {}
    return list(props.keys())


def _body_fields_v3(request_body: Optional[Any], spec: Dict) -> List[str]:
    if not request_body or not isinstance(request_body, dict):
        return []
    request_body = _resolve_ref(request_body, spec)
    content = request_body.get("content") or {}
    for media_type in (
        "application/json",
        "application/x-www-form-urlencoded",
        "multipart/form-data",
    ):
        media  = content.get(media_type) or {}
        schema = _resolve_ref(media.get("schema") or {}, spec)
        if isinstance(schema, dict):
            props = schema.get("properties") or {}
            if props:
                return list(props.keys())
    return []


def _resolve_ref(obj: Any, spec: Dict) -> Any:
    """Follow a local $ref pointer within the spec document."""
    if not isinstance(obj, dict) or "$ref" not in obj:
        return obj
    ref = obj["$ref"]
    if not ref.startswith("#/"):
        return obj
    parts = ref.lstrip("#/").split("/")
    cur: Any = spec
    for part in parts:
        if not isinstance(cur, dict):
            return obj
        cur = cur.get(part, obj)
    return cur


_RE_UUID_HINT = re.compile(r"uuid|guid", re.IGNORECASE)


def _placeholder_for(param_name: str) -> str:
    """Choose an example value for a path parameter based on its name."""
    if _RE_UUID_HINT.search(param_name):
        return "00000000-0000-4000-a000-000000000000"
    return "1"


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

# Canonical paths where OpenAPI specs are commonly served.
_SPEC_PATHS: List[str] = [
    "/openapi.json",
    "/openapi.yaml",
    "/swagger.json",
    "/swagger.yaml",
    "/api-docs",
    "/api-docs.json",
    "/v1/openapi.json",
    "/v2/openapi.json",
    "/v3/openapi.json",
    "/api/openapi.json",
    "/api/swagger.json",
    "/api/v1/openapi.json",
    "/api/v2/openapi.json",
    "/docs/openapi.json",
    "/docs/swagger.json",
    # Swagger UI — the HTML page embeds the spec URL in the page source
    "/swagger-ui.html",
    "/swagger-ui/",
    "/api/swagger-ui.html",
    "/docs",
    "/redoc",
]

_SPEC_URL_RE = re.compile(
    r"""["']((?:https?://[^"']+)?/[^"']*(?:openapi|swagger)[^"']*\.(?:json|yaml))["']""",
    re.IGNORECASE,
)


def discover_openapi(base_url: str, timeout: int = 10) -> Optional[str]:
    """Probe common paths on *base_url* to find an OpenAPI/Swagger spec.

    Probes each path in ``_SPEC_PATHS``.  If the response looks like a valid
    spec (JSON ``swagger``/``openapi`` key, or YAML equivalent), returns the
    full spec URL.  For Swagger UI HTML pages, the embedded spec URL is
    extracted from the page source.

    Args:
        base_url: Root URL of the target (scheme + host, e.g.
                  ``https://api.example.com``).
        timeout:  Per-request timeout in seconds (default 10).

    Returns:
        The URL of the discovered spec, or ``None`` if none was found.
    """
    import urllib.request

    origin = base_url.rstrip("/")

    for path in _SPEC_PATHS:
        url = origin + path
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "commonhuman-core/openapi-discover"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                if resp.status >= 400:
                    continue
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read(65536).decode("utf-8", errors="replace")
        except Exception:
            continue

        # Direct JSON spec
        if "json" in content_type or raw.lstrip().startswith("{"):
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and (
                    "swagger" in data or "openapi" in data or "paths" in data
                ):
                    logger.info("openapi: discovered spec at %s", url)
                    return url
            except (json.JSONDecodeError, ValueError):
                pass

        # YAML spec
        if "yaml" in content_type or path.endswith(".yaml"):
            try:
                import yaml  # type: ignore[import-untyped]
                data = yaml.safe_load(raw)
                if isinstance(data, dict) and (
                    "swagger" in data or "openapi" in data or "paths" in data
                ):
                    logger.info("openapi: discovered YAML spec at %s", url)
                    return url
            except Exception:
                pass

        # HTML page (Swagger UI / Redoc) — extract embedded spec URL
        if "html" in content_type or path.endswith((".html", "/")):
            m = _SPEC_URL_RE.search(raw)
            if m:
                embedded = m.group(1)
                spec_url = embedded if embedded.startswith("http") else origin + embedded
                logger.info("openapi: found embedded spec URL %s in %s", spec_url, url)
                return spec_url

    logger.debug("openapi: no spec discovered on %s", origin)
    return None
