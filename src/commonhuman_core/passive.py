# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Passive analysis helpers for CommonHuman-Lab scanners."""

from __future__ import annotations

from typing import Optional

from requests import Response

from .http.client import HttpClient


def fetch_seed(injector: HttpClient, url: str) -> Optional[Response]:
    """Fetch ``url`` once for passive analysis.

    Returns the :class:`~requests.Response` on success, or ``None`` if the
    request fails or returns a 4xx/5xx status.
    """
    try:
        resp = injector.get(url)
    except Exception:
        return None
    return resp if resp.status_code < 400 else None
