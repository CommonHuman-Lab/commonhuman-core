# commonhuman-core

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/commonhuman-core.svg)](https://pypi.org/project/commonhuman-core/)
[![License](https://img.shields.io/badge/License-AGPLv3-green.svg)](LICENSE)

**Shared HTTP engine and web crawler for CommonHuman-Lab tools** — session management, injection helpers, BFS crawling, and passive recon primitives. One place. No duplication.

```bash
pip install commonhuman-core
pip install commonhuman-core[browser]   # + headless Chromium crawler (requires selenium)
pip install commonhuman-core[openapi]   # + YAML OpenAPI/Swagger support (requires pyyaml)
```

---

## Why it exists

Every CommonHuman-Lab scanner needs to speak HTTP: proxy routing, cookie injection, rate-limit back-off, and injection helpers for query params, POST bodies, path segments, headers, and cookies. Every scanner also needs to crawl — BFS traversal, form discovery, same-origin enforcement.

`commonhuman-core` is the single source of truth for that layer. Tools that use it get:

- **Battle-tested session handling** — automatic retry on connection errors, 429 back-off with `Retry-After` support, configurable per-request delay.
- **A complete injection toolkit** — GET params, form POST, JSON POST, path segments (by index), cookies, and custom headers through one consistent interface.
- **BFS crawling with exclude patterns** — multi-threaded, depth and page limits, HTML form extraction, URL parameter discovery, regex-based URL filtering.
- **A single place to improve** — a new injection method or crawler feature lands in every tool at once.

---

## Quick start

```python
from commonhuman_core.http import HttpClient
from commonhuman_core.crawler import crawl, CrawlResult
from commonhuman_core.passive import fetch_seed
from commonhuman_core.auth import form_login, bearer_login
from commonhuman_core.openapi import load_openapi, ApiEndpoint
from commonhuman_core.browser_crawler import browser_crawl
```

---

## What's in it

| Module | Purpose |
| ------ | ------- |
| `commonhuman_core.http.HttpClient` | HTTP session wrapper — proxy, cookies, SSL, retry, rate limiting, injection helpers |
| `commonhuman_core.http.parse_cookie_string` | Parse `name=value; ...` or JSON cookie strings |
| `commonhuman_core.http.parse_post_data` | Parse urlencoded or JSON POST bodies into a flat dict |
| `commonhuman_core.crawler` | BFS web crawler — link + form discovery, page source storage |
| `commonhuman_core.passive` | Passive recon helpers — `fetch_seed()` |
| `commonhuman_core.auth` | Form login, OAuth2 bearer, CSRF extraction — returns cookies + headers |
| `commonhuman_core.openapi` | OpenAPI 2.x / 3.x spec parser — expands paths to scannable `ApiEndpoint` list |
| `commonhuman_core.browser_crawler` | Headless Chromium BFS URL discovery for JS-rendered SPAs (optional: selenium) |

---

## Modules

### `http.HttpClient`

Thin wrapper around `requests.Session` with everything a scanner needs built in.

```python
from commonhuman_core.http import HttpClient

client = HttpClient(
    timeout=15,
    proxy="http://127.0.0.1:8080",
    headers={"X-Custom": "value"},
    cookies="session=abc; token=xyz",
    verify_ssl=False,
    delay=0.5,          # seconds between requests
)
```

#### Core HTTP

```python
resp = client.get("https://target.com/search?q=test")
resp = client.post("https://target.com/login", data={"user": "admin"})
resp = client.head("https://target.com/")

print(client.request_count)  # total requests sent (including retries)
client.close()
```

#### Injection helpers

```python
# Replace or add a query parameter
client.inject_get("https://target.com/search?q=original", "q", "PAYLOAD")
# → GET /search?q=PAYLOAD

# Inject into a form POST body
client.inject_post("https://target.com/login", "user", "PAYLOAD", base_data={"csrf": "tok"})
# → POST body: user=PAYLOAD&csrf=tok

# Inject into a JSON POST body
client.inject_post_json("https://target.com/api/search", "query", "PAYLOAD", base_data={"page": 1})
# → POST body: {"query": "PAYLOAD", "page": 1}

# Replace a path segment by index (0-based after splitting on "/")
client.inject_path("https://target.com/api/user/123", 3, "PAYLOAD")
# → GET /api/user/PAYLOAD

# Pass -1 to append a new trailing segment
client.inject_path("https://target.com/page", -1, "PAYLOAD")
# → GET /page/PAYLOAD

# Inject a cookie for a single request
client.inject_cookie("https://target.com/", "session", "PAYLOAD")

# Inject a custom header for a single request
client.inject_header("https://target.com/", "X-Forwarded-For", "PAYLOAD")
```

#### URL utilities

```python
HttpClient.get_params("https://target.com/?a=1&b=2")           # → ["a", "b"]
HttpClient.get_base_url("https://target.com/path?q=1")         # → "https://target.com"
HttpClient.same_origin("https://target.com/a", "https://other.com/b")  # → False
```

#### Rate limiting

Automatic 429 back-off with `Retry-After` header support. Up to 2 retries per request, 5-second default back-off.

```python
# Handled transparently — no extra code needed
resp = client.get("https://target.com/api/")
```

---

### `crawler`

Multi-threaded BFS crawler. Discovers pages, forms, and URL parameters within a target origin.

```python
from commonhuman_core.http import HttpClient
from commonhuman_core.crawler import crawl, CrawlResult, FormTarget

client = HttpClient(delay=0.2)
result: CrawlResult = crawl(
    "https://target.com/",
    client,
    max_pages=50,
    max_depth=3,
    threads=5,
    same_origin=True,
    exclude_patterns=[r"/logout", r"\.pdf$"],
)

result.visited_urls   # list of all crawled URLs
result.form_targets   # list of FormTarget — each a discovered HTML form
result.url_params     # list of (url, [param_names]) for URLs with query params
result.page_sources   # dict of {url: html} — raw page content
```

`FormTarget` carries everything needed to replay a form submission:

```python
for form in result.form_targets:
    print(form.method, form.action)
    print(form.params)     # {"username": "", "password": ""} — injectable fields
    print(form.base_data)  # {"csrf": "abc", "_submit": "Login"} — non-injectable
```

`exclude_patterns` accepts a list of regex strings. Any URL matching one is silently skipped before fetching.

---

### `passive`

```python
from commonhuman_core.passive import fetch_seed
from commonhuman_core.http import HttpClient

client = HttpClient()
resp = fetch_seed(client, "https://target.com/")
# Returns None on connection error or 4xx/5xx — safe to call without a try/except
if resp:
    print(resp.text)
```

Useful for a single passive check before starting an active scan — confirms the target is reachable and returns a response worth analysing.

---

### `auth`

Authenticate against a login form or OAuth2 token endpoint before scanning. Returns cookies and headers that can be forwarded to any `HttpClient`.

```python
from commonhuman_core.auth import form_login, bearer_login

# Form-based login — GET page, extract CSRF, POST credentials
auth = form_login(
    login_url="https://target.com/login",
    username="admin",
    password="secret",
    # username_field="username",  # default
    # password_field="password",  # default
)
print(auth.cookies)   # "session=abc; csrf=xyz"
print(auth.headers)   # {"Authorization": "Bearer ..."} if JSON token returned

# OAuth2 client-credentials
auth = bearer_login(
    token_url="https://target.com/oauth/token",
    client_id="my-client",
    client_secret="my-secret",
)
```

`auth.is_empty()` returns `True` when login produced no usable credentials (useful for early-exit checks).

---

### `openapi`

Parse an OpenAPI 2.x (Swagger) or 3.x spec and expand every path into a list of ready-to-scan URLs. Path parameters like `{id}` are substituted with sensible placeholders (`1` for integers, a fixed UUID for UUID params).

```python
from commonhuman_core.openapi import load_openapi

# Accepts a file path, a URL, or a raw JSON/YAML string
endpoints = load_openapi("https://target.com/openapi.json", base_url="https://target.com")
endpoints = load_openapi("/path/to/swagger.yaml")   # requires pyyaml

for ep in endpoints:
    print(ep.method, ep.url)        # GET https://target.com/users/1
    print(ep.query_params)          # ["filter", "page"]
    print(ep.body_params)           # ["name", "email"]
```

YAML support requires the optional `pyyaml` dependency:

```bash
pip install commonhuman-core[openapi]
```

---

### `browser_crawler`

Headless Chromium BFS crawler that discovers URLs from JavaScript-rendered pages — invisible to HTTP-layer crawlers. Returns a flat list of same-origin URLs found across rendered DOM links.

```python
from commonhuman_core.browser_crawler import browser_crawl

urls = browser_crawl(
    start_url="https://target.com/",
    max_pages=50,
    max_depth=3,
    cookies="session=abc",          # injected before crawling
    headless=True,
    # chromium_path="/usr/bin/chromium",   # auto-detected by default
    # chromedriver_path="/usr/bin/chromedriver",
    spa_wait_s=1.5,                 # seconds to wait for JS to settle per page
)

for url in urls:
    print(url)
```

Requires the optional `selenium` dependency:

```bash
pip install commonhuman-core[browser]
```

---

## Subclassing for tool-specific methods

`HttpClient` is designed to be subclassed. `stingxss` adds XSS reflection probing on top:

```python
from commonhuman_core.http import HttpClient

class Injector(HttpClient):
    def probe_reflection(self, url, param, marker, method="GET"):
        ...

    def probe_header_reflection(self, url, header_name, marker):
        ...
```

`breachsql` uses `HttpClient` directly — no subclass needed.

---

## Design principles

- **Transport only** — `commonhuman-core` handles HTTP, crawling, and passive recon. Vulnerability detection, payload generation, and result analysis belong in the tools that use it.
- **One consistent interface** — every injection method follows the same call shape: `(url, target, payload)`. No special cases per method type.
- **Threaded by default, deterministic when needed** — `crawl()` uses a thread pool; pass `threads=1` for single-threaded sequential crawling.
- **100% branch coverage enforced** — `pytest --cov` with `fail_under=100` in CI. Every branch in every module is tested.

---

## Tests

```bash
git clone https://github.com/commonhuman-lab/commonhuman-core.git
cd commonhuman-core
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
pytest tests/unit/        # isolated unit tests only
pytest tests/regression/  # API surface contracts (requires stingxss + breachsql installed)
```

---

## License

Licensed under the [AGPLv3](LICENSE).
You are free to use, modify, and distribute this software. If you run it as a service or distribute it, the source must remain open.

For commercial licensing, contact the author.
