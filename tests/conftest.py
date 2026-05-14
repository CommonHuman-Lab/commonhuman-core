# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Shared pytest fixtures for commonhuman-core tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from commonhuman_core.http import HttpClient


# ---------------------------------------------------------------------------
# Response factory
# ---------------------------------------------------------------------------


def _make_resp(status: int = 200, text: str = "OK", headers: dict | None = None,
               url: str = "https://example.com/") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text        = text
    resp.headers     = headers or {"content-type": "text/html"}
    resp.url         = url
    return resp


@pytest.fixture
def make_resp():
    return _make_resp


# ---------------------------------------------------------------------------
# HttpClient with mocked session
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """A fresh HttpClient with no real network."""
    return HttpClient(timeout=5, delay=0.0)


@pytest.fixture
def mock_client(client):
    """HttpClient whose underlying session is fully mocked."""
    with patch.object(client, "_session") as mock_session:
        yield client, mock_session
