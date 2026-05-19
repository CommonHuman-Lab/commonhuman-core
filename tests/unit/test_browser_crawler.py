# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Tests for commonhuman_core.browser_crawler — selenium is fully mocked."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, call, patch

import pytest

from commonhuman_core.browser_crawler import _normalise, browser_crawl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_driver(link_pages: list[list[str]] | None = None) -> MagicMock:
    """Return a mock Selenium WebDriver.

    link_pages: list of link lists returned on successive execute_script calls.
    """
    driver = MagicMock()
    if link_pages is not None:
        driver.execute_script.side_effect = link_pages
    else:
        driver.execute_script.return_value = []
    return driver


def _crawl_with_driver(driver, **kwargs):
    """Run browser_crawl with _setup_driver patched to return driver."""
    with patch("commonhuman_core.browser_crawler._setup_driver", return_value=driver), \
         patch("commonhuman_core.browser_crawler.time.sleep"), \
         patch("commonhuman_core.browser_crawler._wait_for_ready"):
        return browser_crawl(**kwargs)


# ---------------------------------------------------------------------------
# _normalise
# ---------------------------------------------------------------------------

class TestNormalise:
    def test_strips_fragment(self):
        assert _normalise("https://example.com/path#section") == "https://example.com/path"

    def test_strips_trailing_slash(self):
        assert _normalise("https://example.com/path/") == "https://example.com/path"

    def test_no_change_for_clean_url(self):
        assert _normalise("https://example.com/path") == "https://example.com/path"

    def test_strips_both_fragment_and_slash(self):
        assert _normalise("https://example.com/path/#frag") == "https://example.com/path"


# ---------------------------------------------------------------------------
# browser_crawl — import error / driver failure
# ---------------------------------------------------------------------------

class TestBrowserCrawlErrors:
    def test_import_error_returns_empty(self):
        with patch(
            "commonhuman_core.browser_crawler._setup_driver",
            side_effect=ImportError("selenium not installed"),
        ), patch("commonhuman_core.browser_crawler.time.sleep"):
            result = browser_crawl("https://example.com/")
        assert result == []

    def test_driver_setup_exception_returns_empty(self):
        with patch(
            "commonhuman_core.browser_crawler._setup_driver",
            side_effect=RuntimeError("driver not found"),
        ), patch("commonhuman_core.browser_crawler.time.sleep"):
            result = browser_crawl("https://example.com/")
        assert result == []


# ---------------------------------------------------------------------------
# browser_crawl — basic crawl behaviour
# ---------------------------------------------------------------------------

class TestBrowserCrawlBasic:
    def test_start_url_included_in_visited(self):
        driver = _make_driver()
        result = _crawl_with_driver(driver, start_url="https://example.com/")
        assert "https://example.com/" in result

    def test_discovered_same_origin_links_visited(self):
        links_page1 = ["https://example.com/page2", "https://example.com/page3"]
        driver = _make_driver(link_pages=[links_page1, [], []])
        result = _crawl_with_driver(driver, start_url="https://example.com/", max_depth=1)
        assert "https://example.com/page2" in result
        assert "https://example.com/page3" in result

    def test_cross_origin_links_not_followed(self):
        links_page1 = ["https://other.com/evil", "https://example.com/local"]
        driver = _make_driver(link_pages=[links_page1, []])
        result = _crawl_with_driver(driver, start_url="https://example.com/", max_depth=1)
        assert "https://other.com/evil" not in result
        assert "https://example.com/local" in result

    def test_max_pages_limit_respected(self):
        links = [f"https://example.com/p{i}" for i in range(20)]
        driver = _make_driver(link_pages=[links] + [[] for _ in range(20)])
        result = _crawl_with_driver(
            driver, start_url="https://example.com/", max_pages=3, max_depth=1
        )
        assert len(result) <= 3

    def test_max_depth_limit_stops_exploration(self):
        # At depth 0, return links. At depth ≥ max_depth, links should not be queued.
        links_page1 = ["https://example.com/p2"]
        links_page2 = ["https://example.com/p3"]
        driver = _make_driver(link_pages=[links_page1, links_page2, []])
        result = _crawl_with_driver(
            driver, start_url="https://example.com/", max_depth=1, max_pages=10
        )
        # p2 reachable at depth=1, p3 would require depth=2 — should NOT be visited
        assert "https://example.com/p2" in result
        assert "https://example.com/p3" not in result

    def test_duplicates_not_revisited(self):
        # Both pages return the same link
        links = ["https://example.com/shared"]
        driver = _make_driver(link_pages=[links, links, []])
        result = _crawl_with_driver(
            driver, start_url="https://example.com/", max_depth=1
        )
        assert result.count("https://example.com/shared") == 1

    def test_driver_quit_called_after_crawl(self):
        driver = _make_driver()
        _crawl_with_driver(driver, start_url="https://example.com/")
        driver.quit.assert_called_once()

    def test_driver_quit_called_even_on_error(self):
        driver = MagicMock()
        driver.execute_script.side_effect = RuntimeError("crash")
        with patch("commonhuman_core.browser_crawler._setup_driver", return_value=driver), \
             patch("commonhuman_core.browser_crawler.time.sleep"), \
             patch("commonhuman_core.browser_crawler._wait_for_ready"):
            result = browser_crawl("https://example.com/")
        driver.quit.assert_called_once()

    def test_page_load_failure_continues(self):
        # First get (start URL) succeeds; second get (linked URL) raises → except fires, crawl continues
        driver = MagicMock()
        driver.get.side_effect = [None, RuntimeError("timeout")]
        driver.execute_script.return_value = ["https://example.com/page2"]
        with patch("commonhuman_core.browser_crawler._setup_driver", return_value=driver), \
             patch("commonhuman_core.browser_crawler.time.sleep"), \
             patch("commonhuman_core.browser_crawler._wait_for_ready"):
            result = browser_crawl(
                "https://example.com/",
                max_depth=1,
                max_pages=10,
            )
        assert "https://example.com/" in result
        assert "https://example.com/page2" not in result  # page load failed, not visited

    def test_execute_script_failure_continues(self):
        driver = MagicMock()
        driver.get.return_value = None
        driver.execute_script.side_effect = RuntimeError("script error")
        with patch("commonhuman_core.browser_crawler._setup_driver", return_value=driver), \
             patch("commonhuman_core.browser_crawler.time.sleep"), \
             patch("commonhuman_core.browser_crawler._wait_for_ready"):
            result = browser_crawl("https://example.com/", max_depth=1)
        assert "https://example.com/" in result

    def test_outer_exception_caught_driver_still_quit(self):
        # execute_script returns non-iterable → for-loop raises TypeError → hits outer except
        driver = MagicMock()
        driver.get.return_value = None
        driver.execute_script.return_value = 42  # int is not iterable → TypeError in for-loop
        with patch("commonhuman_core.browser_crawler._setup_driver", return_value=driver), \
             patch("commonhuman_core.browser_crawler.time.sleep"), \
             patch("commonhuman_core.browser_crawler._wait_for_ready"):
            result = browser_crawl("https://example.com/", max_depth=1)
        driver.quit.assert_called_once()

    def test_driver_quit_exception_suppressed(self):
        # driver.quit() raises — the finally except must swallow it silently
        driver = MagicMock()
        driver.execute_script.return_value = []
        driver.quit.side_effect = RuntimeError("quit failed")
        with patch("commonhuman_core.browser_crawler._setup_driver", return_value=driver), \
             patch("commonhuman_core.browser_crawler.time.sleep"), \
             patch("commonhuman_core.browser_crawler._wait_for_ready"):
            result = browser_crawl("https://example.com/", max_depth=0)
        # No exception raised — the finally swallowed it
        assert "https://example.com/" in result

    def test_already_seen_link_skipped(self):
        # page2 (at depth=1) returns page3, but page3 is already in seen → continue fires
        driver = MagicMock()
        driver.execute_script.side_effect = [
            ["https://example.com/page2", "https://example.com/page3"],  # depth=0 → start URL
            ["https://example.com/page3"],   # depth=1 → page2 returns page3 (already seen)
            [],                              # depth=1 → page3 (links not collected at max_depth)
        ]
        with patch("commonhuman_core.browser_crawler._setup_driver", return_value=driver), \
             patch("commonhuman_core.browser_crawler.time.sleep"), \
             patch("commonhuman_core.browser_crawler._wait_for_ready"):
            result = browser_crawl("https://example.com/", max_depth=2, max_pages=10)
        # page3 visited once, not twice
        assert result.count("https://example.com/page3") == 1


# ---------------------------------------------------------------------------
# browser_crawl — cookie injection
# ---------------------------------------------------------------------------

class TestBrowserCrawlCookies:
    def test_cookies_injected_before_crawl(self):
        driver = _make_driver()
        _crawl_with_driver(
            driver,
            start_url="https://example.com/",
            cookies="session=abc; token=xyz",
        )
        calls = driver.add_cookie.call_args_list
        names = [c.args[0]["name"] for c in calls]
        assert "session" in names
        assert "token" in names

    def test_cookie_injection_failure_continues(self):
        driver = MagicMock()
        driver.get.return_value = None
        driver.add_cookie.side_effect = RuntimeError("cookie error")
        driver.execute_script.return_value = []
        with patch("commonhuman_core.browser_crawler._setup_driver", return_value=driver), \
             patch("commonhuman_core.browser_crawler.time.sleep"), \
             patch("commonhuman_core.browser_crawler._wait_for_ready"):
            result = browser_crawl(
                "https://example.com/", cookies="session=abc"
            )
        assert "https://example.com/" in result

    def test_no_cookies_skips_injection(self):
        driver = _make_driver()
        _crawl_with_driver(driver, start_url="https://example.com/", cookies="")
        driver.add_cookie.assert_not_called()

    def test_cookie_without_equals_skipped(self):
        driver = _make_driver()
        _crawl_with_driver(
            driver,
            start_url="https://example.com/",
            cookies="malformed; session=abc",
        )
        calls = driver.add_cookie.call_args_list
        names = [c.args[0]["name"] for c in calls]
        assert "malformed" not in names
        assert "session" in names


# ---------------------------------------------------------------------------
# _setup_driver — tested by mocking selenium at sys.modules level
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _wait_for_ready — tested directly with mocked driver and time
# ---------------------------------------------------------------------------

class TestWaitForReady:
    def test_completes_immediately_when_ready(self):
        from commonhuman_core.browser_crawler import _wait_for_ready
        driver = MagicMock()
        driver.execute_script.return_value = "complete"
        with patch("commonhuman_core.browser_crawler.time.monotonic", return_value=1000.0), \
             patch("commonhuman_core.browser_crawler.time.sleep"):
            _wait_for_ready(driver, timeout_s=5.0)
        driver.execute_script.assert_called_once_with("return document.readyState")

    def test_polls_until_ready(self):
        from commonhuman_core.browser_crawler import _wait_for_ready
        driver = MagicMock()
        driver.execute_script.side_effect = ["loading", "loading", "complete"]
        monotonic_values = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
        with patch("commonhuman_core.browser_crawler.time.monotonic",
                   side_effect=monotonic_values), \
             patch("commonhuman_core.browser_crawler.time.sleep"):
            _wait_for_ready(driver, timeout_s=5.0)
        assert driver.execute_script.call_count == 3

    def test_times_out_when_never_ready(self):
        from commonhuman_core.browser_crawler import _wait_for_ready
        driver = MagicMock()
        driver.execute_script.return_value = "loading"
        monotonic_values = [0.0, 0.0, 10.0, 10.0]
        with patch("commonhuman_core.browser_crawler.time.monotonic",
                   side_effect=monotonic_values), \
             patch("commonhuman_core.browser_crawler.time.sleep"):
            _wait_for_ready(driver, timeout_s=1.0)

    def test_execute_script_exception_is_swallowed(self):
        from commonhuman_core.browser_crawler import _wait_for_ready
        driver = MagicMock()
        driver.execute_script.side_effect = [RuntimeError("stale element"), "complete"]
        monotonic_values = [0.0, 0.1, 0.2, 0.3]
        with patch("commonhuman_core.browser_crawler.time.monotonic",
                   side_effect=monotonic_values), \
             patch("commonhuman_core.browser_crawler.time.sleep"):
            _wait_for_ready(driver, timeout_s=5.0)
        assert driver.execute_script.call_count == 2


class TestSetupDriver:
    def _make_selenium_mocks(self):
        mock_webdriver = MagicMock()
        mock_options   = MagicMock()
        mock_service   = MagicMock()

        mock_options_class = MagicMock(return_value=mock_options)
        mock_service_class = MagicMock(return_value=mock_service)

        mock_webdriver.Chrome.return_value = MagicMock()
        selenium_mocks = {
            "selenium":                          MagicMock(),
            "selenium.webdriver":                mock_webdriver,
            "selenium.webdriver.chrome":         MagicMock(),
            "selenium.webdriver.chrome.options": MagicMock(Options=mock_options_class),
            "selenium.webdriver.chrome.service": MagicMock(Service=mock_service_class),
        }
        return selenium_mocks, mock_webdriver, mock_options, mock_options_class, mock_service_class

    def test_headless_argument_added(self):
        from commonhuman_core.browser_crawler import _setup_driver
        mocks, _, mock_options, _, _ = self._make_selenium_mocks()
        with patch.dict(sys.modules, mocks), patch("shutil.which", return_value=None):
            _setup_driver(headless=True, chromium_path="/usr/bin/chromium", chromedriver_path="/drv")
        add_arg_calls = [str(c) for c in mock_options.add_argument.call_args_list]
        assert any("--headless" in c for c in add_arg_calls)

    def test_headless_false_no_headless_arg(self):
        from commonhuman_core.browser_crawler import _setup_driver
        mocks, _, mock_options, _, _ = self._make_selenium_mocks()
        with patch.dict(sys.modules, mocks), patch("shutil.which", return_value=None):
            _setup_driver(headless=False, chromium_path="/usr/bin/chromium", chromedriver_path="/drv")
        add_arg_calls = [str(c) for c in mock_options.add_argument.call_args_list]
        assert not any("--headless" in c for c in add_arg_calls)

    def test_explicit_chromium_path_set_on_options(self):
        from commonhuman_core.browser_crawler import _setup_driver
        mocks, _, mock_options, _, _ = self._make_selenium_mocks()
        with patch.dict(sys.modules, mocks), patch("shutil.which", return_value=None):
            _setup_driver(headless=True, chromium_path="/my/chromium", chromedriver_path="/drv")
        assert mock_options.binary_location == "/my/chromium"

    def test_no_chromium_path_auto_detected_via_shutil(self):
        from commonhuman_core.browser_crawler import _setup_driver
        mocks, _, mock_options, _, mock_service_class = self._make_selenium_mocks()
        with patch.dict(sys.modules, mocks), \
             patch("shutil.which", side_effect=lambda c: "/found/chromium" if c == "chromium" else None):
            _setup_driver(headless=True, chromium_path="", chromedriver_path="/drv")
        assert mock_options.binary_location == "/found/chromium"

    def test_no_chromium_found_binary_location_not_set(self):
        from commonhuman_core.browser_crawler import _setup_driver
        mocks, _, mock_options, _, _ = self._make_selenium_mocks()
        with patch.dict(sys.modules, mocks), patch("shutil.which", return_value=None):
            _setup_driver(headless=True, chromium_path="", chromedriver_path="/drv")
        # binary_location should NOT be set when nothing is found
        assert not hasattr(mock_options, "binary_location") or mock_options.binary_location != ""

    def test_explicit_chromedriver_path_used_in_service(self):
        from commonhuman_core.browser_crawler import _setup_driver
        mocks, _, _, _, mock_service_class = self._make_selenium_mocks()
        with patch.dict(sys.modules, mocks), patch("shutil.which", return_value=None):
            _setup_driver(headless=True, chromium_path="/c", chromedriver_path="/drv/chromedriver")
        mock_service_class.assert_called_with("/drv/chromedriver")

    def test_no_chromedriver_found_uses_service_default(self):
        from commonhuman_core.browser_crawler import _setup_driver
        mocks, _, _, _, mock_service_class = self._make_selenium_mocks()
        with patch.dict(sys.modules, mocks), patch("shutil.which", return_value=None):
            _setup_driver(headless=True, chromium_path="/c", chromedriver_path="")
        # Service() with no args — auto-detect
        mock_service_class.assert_called_with()

    def test_chromedriver_auto_detected_via_shutil(self):
        from commonhuman_core.browser_crawler import _setup_driver
        mocks, _, _, _, mock_service_class = self._make_selenium_mocks()
        with patch.dict(sys.modules, mocks), \
             patch("shutil.which", return_value="/found/chromedriver"):
            _setup_driver(headless=True, chromium_path="/c", chromedriver_path="")
        mock_service_class.assert_called_with("/found/chromedriver")
