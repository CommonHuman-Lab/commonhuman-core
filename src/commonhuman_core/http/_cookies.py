# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Cookie string and POST body parsing helpers."""

from __future__ import annotations

import json
import urllib.parse as up
from typing import Dict


def parse_cookie_string(cookies: str) -> Dict[str, str]:
    """Parse ``'name=value; name2=value2'`` or a JSON object string into a dict."""
    cookies = cookies.strip()
    if cookies.startswith("{"):
        try:
            return json.loads(cookies)
        except Exception:
            pass
    result: Dict[str, str] = {}
    for part in cookies.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            result[k.strip()] = v.strip()
    return result


def parse_post_data(raw: str) -> Dict[str, str]:
    """Parse a raw POST body — supports ``application/x-www-form-urlencoded`` and JSON.

    Returns a flat ``{key: value}`` dict.
    """
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            return {str(k): str(v) for k, v in data.items()}
        except Exception:
            pass
    parsed = up.parse_qs(raw, keep_blank_values=True)
    return {k: v[0] if v else "" for k, v in parsed.items()}
