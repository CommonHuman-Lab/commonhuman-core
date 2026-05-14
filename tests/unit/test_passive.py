# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Tests for commonhuman_core.passive."""

from unittest.mock import MagicMock

from commonhuman_core.passive import fetch_seed
from commonhuman_core.http import HttpClient


def _inj(status: int = 200) -> HttpClient:
    inj  = MagicMock(spec=HttpClient)
    resp = MagicMock()
    resp.status_code = status
    inj.get.return_value = resp
    return inj


class TestFetchSeed:
    def test_200_returns_response(self):
        inj    = _inj(200)
        result = fetch_seed(inj, "https://example.com/")
        assert result is not None
        assert result.status_code == 200

    def test_4xx_returns_none(self):
        assert fetch_seed(_inj(403), "https://example.com/") is None

    def test_5xx_returns_none(self):
        assert fetch_seed(_inj(500), "https://example.com/") is None

    def test_exception_returns_none(self):
        inj = MagicMock(spec=HttpClient)
        inj.get.side_effect = OSError("connection refused")
        assert fetch_seed(inj, "https://example.com/") is None

    def test_399_returns_response(self):
        assert fetch_seed(_inj(399), "https://example.com/") is not None
