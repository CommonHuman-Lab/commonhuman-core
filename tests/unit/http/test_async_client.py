# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 CommonHuman-Lab
"""Tests for AsyncHttpClient — concurrency control and batch execution."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from commonhuman_core.http.async_client import AsyncHttpClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status: int = 200, text: str = "OK") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers     = {}
    resp.text        = text
    return resp


def _client(concurrency: int | None = None, **kwargs) -> AsyncHttpClient:
    kwargs.setdefault("timeout", 5)
    kwargs.setdefault("delay", 0.0)
    return AsyncHttpClient(concurrency=concurrency, **kwargs)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

class TestInit:
    def test_no_concurrency_creates_no_semaphore(self):
        c = _client()
        assert c._semaphore is None

    def test_concurrency_creates_semaphore(self):
        c = _client(concurrency=5)
        assert c._semaphore is not None
        assert isinstance(c._semaphore, asyncio.Semaphore)

    def test_delay_clamped_to_zero(self):
        c = _client(delay=-1.0)
        assert c.delay == 0.0

    def test_request_count_starts_at_zero(self):
        c = _client()
        assert c.request_count == 0


# ---------------------------------------------------------------------------
# Concurrency limiting
# ---------------------------------------------------------------------------

class TestConcurrencyLimit:
    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_requests(self):
        """No more than N requests should be in-flight simultaneously."""
        concurrency = 3
        c = _client(concurrency=concurrency)

        in_flight = 0
        peak      = 0

        async def _fake_request(*_args, **_kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return _mock_response()

        with patch.object(c._client, "get", side_effect=_fake_request):
            await asyncio.gather(*[c.get("http://example.com/") for _ in range(10)])

        assert peak <= concurrency

    @pytest.mark.asyncio
    async def test_no_semaphore_allows_all_concurrent(self):
        """Without a concurrency cap, all requests fire immediately."""
        c = _client(concurrency=None)

        started = []

        async def _fake_request(*_args, **_kwargs):
            started.append(1)
            await asyncio.sleep(0.05)
            return _mock_response()

        tasks = [asyncio.create_task(c.get("http://example.com/")) for _ in range(5)]

        with patch.object(c._client, "get", side_effect=_fake_request):
            # All 5 should have started before the first one finishes
            await asyncio.gather(*tasks)

        assert len(started) == 5

    @pytest.mark.asyncio
    async def test_request_count_increments(self):
        c = _client()
        with patch.object(c._client, "get", return_value=_mock_response()):
            await c.get("http://example.com/")
            await c.get("http://example.com/")
        assert c.request_count == 2

    @pytest.mark.asyncio
    async def test_head_respects_semaphore(self):
        c = _client(concurrency=1)
        order: list[int] = []

        async def _slow(*_a, **_kw):
            order.append(1)
            await asyncio.sleep(0.02)
            return _mock_response()

        with patch.object(c._client, "head", side_effect=_slow):
            await asyncio.gather(c.head("http://example.com/"), c.head("http://example.com/"))

        assert len(order) == 2


# ---------------------------------------------------------------------------
# request_batch
# ---------------------------------------------------------------------------

class TestRequestBatch:
    @pytest.mark.asyncio
    async def test_yields_all_responses(self):
        c = _client()
        responses = []

        with patch.object(c._client, "get", return_value=_mock_response(200)):
            async for resp in c.request_batch([
                c.get("http://example.com/a"),
                c.get("http://example.com/b"),
                c.get("http://example.com/c"),
            ]):
                responses.append(resp)

        assert len(responses) == 3

    @pytest.mark.asyncio
    async def test_yields_as_completed_not_in_order(self):
        """Slower requests should not block faster ones."""
        c = _client()
        completion_order: list[str] = []

        async def _slow():
            await asyncio.sleep(0.05)
            completion_order.append("slow")
            return _mock_response(200, "slow")

        async def _fast():
            await asyncio.sleep(0.001)
            completion_order.append("fast")
            return _mock_response(200, "fast")

        async for _ in c.request_batch([_slow(), _fast()]):
            pass

        assert completion_order[0] == "fast"

    @pytest.mark.asyncio
    async def test_empty_batch_yields_nothing(self):
        c = _client()
        results = [r async for r in c.request_batch([])]
        assert results == []

    @pytest.mark.asyncio
    async def test_single_item_batch(self):
        c = _client()
        with patch.object(c._client, "get", return_value=_mock_response(201)):
            results = [r async for r in c.request_batch([c.get("http://example.com/")])]
        assert len(results) == 1
        assert results[0].status_code == 201

    @pytest.mark.asyncio
    async def test_batch_respects_concurrency(self):
        """request_batch should not bypass the semaphore."""
        c = _client(concurrency=2)
        in_flight = 0
        peak      = 0

        async def _timed(*_a, **_kw):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return _mock_response()

        with patch.object(c._client, "get", side_effect=_timed):
            calls = [c.get("http://example.com/") for _ in range(6)]
            async for _ in c.request_batch(calls):
                pass

        assert peak <= 2


# ---------------------------------------------------------------------------
# Existing API unaffected
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    @pytest.mark.asyncio
    async def test_get_still_works_without_concurrency(self):
        c = _client()
        with patch.object(c._client, "get", return_value=_mock_response(200)):
            resp = await c.get("http://example.com/")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_inject_get_still_works(self):
        c = _client()
        with patch.object(c._client, "get", return_value=_mock_response(200)) as m:
            await c.inject_get("http://example.com/?id=1", "id", "2")
        called_url = str(m.call_args[0][0])
        assert "id=2" in called_url

    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self):
        c = _client()
        closed = []
        with patch.object(c._client, "aclose", new_callable=AsyncMock) as mock_close:
            async with c:
                pass
            mock_close.assert_called_once()
