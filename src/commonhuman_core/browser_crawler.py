# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""
commonhuman-core — browser_crawler.py
Headless Chromium-based URL discovery for JavaScript-rendered sites.

Unlike the standard BFS crawler (which parses static HTML), this module
renders each page with Selenium, waits for JavaScript to complete, and
collects all links present in the fully-rendered DOM.  Same-origin only.

Requires: selenium>=4.0
    pip install 'commonhuman-core[browser]'
"""
from __future__ import annotations

import logging
import time
import urllib.parse as up
from collections import deque
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_WAIT_FIRST_PAGE = 2.0   # seconds for initial page load / SPA bootstrap
_WAIT_SUBSEQUENT = 1.5   # seconds for subsequent navigations


def browser_crawl(
    start_url:         str,
    max_pages:         int = 50,
    max_depth:         int = 2,
    headless:          bool = True,
    cookies:           str = "",
    extra_headers:     Optional[Dict[str, str]] = None,
    chromium_path:     str = "",
    chromedriver_path: str = "",
    spa_wait_s:        float = _WAIT_SUBSEQUENT,
) -> List[str]:
    """Discover URLs by rendering pages with headless Chromium.

    Performs BFS from ``start_url``, rendering each page with Selenium and
    collecting ``<a href>`` links from the fully-rendered DOM.  Only follows
    same-origin URLs.  Returns a deduplicated list of visited URLs.

    Parameters
    ----------
    start_url:
        Seed URL to start from.
    max_pages:
        Stop after visiting this many unique pages.
    max_depth:
        Maximum BFS depth from start_url.
    headless:
        Run Chromium without a visible window (default True).
    cookies:
        Cookie string injected before the first request (``name=val; name2=val2``).
    extra_headers:
        Not injected at the driver level (Selenium has limited header support);
        reserved for future CDP-based header injection.
    chromium_path:
        Path to Chromium binary.  Auto-detected if empty.
    chromedriver_path:
        Path to chromedriver binary.  Auto-detected if empty.
    spa_wait_s:
        Seconds to wait after each page navigation for JS to render.
    """
    try:
        driver = _setup_driver(headless, chromium_path, chromedriver_path)
    except ImportError as exc:
        logger.error(
            "browser_crawl requires selenium — pip install 'commonhuman-core[browser]'. %s", exc
        )
        return []
    except Exception as exc:
        logger.error("browser_crawl: failed to start Chromium driver: %s", exc)
        return []

    parsed_start = up.urlparse(start_url)
    origin       = f"{parsed_start.scheme}://{parsed_start.netloc}"

    visited: List[str] = []
    seen:    Set[str]  = set()
    queue:   deque[tuple[str, int]] = deque([(start_url, 0)])
    seen.add(_normalise(start_url))

    try:
        # Inject cookies on the origin before any navigation
        if cookies:
            try:
                driver.get(origin)
                time.sleep(0.3)
                for pair in cookies.split(";"):
                    pair = pair.strip()
                    if "=" in pair:
                        name, _, value = pair.partition("=")
                        driver.add_cookie({"name": name.strip(), "value": value.strip()})
            except Exception as exc:
                logger.debug("browser_crawl: cookie injection failed: %s", exc)

        while queue and len(visited) < max_pages:
            url, depth = queue.popleft()

            try:
                driver.get(url)
                wait = _WAIT_FIRST_PAGE if depth == 0 else spa_wait_s
                time.sleep(wait)
            except Exception as exc:
                logger.debug("browser_crawl: page load failed %s: %s", url, exc)
                continue

            visited.append(url)
            logger.debug("browser_crawl: visited %s (depth=%d)", url, depth)

            if depth >= max_depth:
                continue

            try:
                links: List[str] = driver.execute_script(
                    "return Array.from(document.querySelectorAll('a[href]'))"
                    ".map(a => a.href)"
                    ".filter(h => h.startsWith('http'));"
                ) or []
            except Exception:
                links = []

            for link in links:
                norm = _normalise(link)
                if norm in seen:
                    continue
                parsed = up.urlparse(link)
                if f"{parsed.scheme}://{parsed.netloc}" != origin:
                    continue
                seen.add(norm)
                queue.append((link, depth + 1))

    except Exception as exc:
        logger.warning("browser_crawl: unexpected error: %s", exc)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    logger.info("browser_crawl: discovered %d URL(s) from %s", len(visited), start_url)
    return visited


# ---------------------------------------------------------------------------
# Selenium driver factory
# ---------------------------------------------------------------------------

def _setup_driver(headless: bool, chromium_path: str, chromedriver_path: str):
    from selenium import webdriver  # noqa: PLC0415
    from selenium.webdriver.chrome.options import Options  # noqa: PLC0415
    from selenium.webdriver.chrome.service import Service  # noqa: PLC0415

    opts = Options()
    if headless:
        opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")

    if not chromium_path:
        import shutil
        for candidate in ("chromium", "chromium-browser", "google-chrome"):
            if shutil.which(candidate):
                chromium_path = candidate
                break
    if chromium_path:
        opts.binary_location = chromium_path

    if not chromedriver_path:
        import shutil
        for candidate in ("chromedriver",):
            if shutil.which(candidate):
                chromedriver_path = candidate
                break

    svc    = Service(chromedriver_path) if chromedriver_path else Service()
    driver = webdriver.Chrome(service=svc, options=opts)
    driver.set_page_load_timeout(15)
    return driver


def _normalise(url: str) -> str:
    """Strip fragment for deduplication."""
    parsed = up.urlparse(url)
    return up.urlunparse(parsed._replace(fragment="")).rstrip("/")
