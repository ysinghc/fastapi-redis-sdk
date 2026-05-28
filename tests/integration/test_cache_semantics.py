"""Integration tests for HTTP cache semantics (headers, ETag, Cache-Control).

Tests the DI-based ``cache()`` dependency through a full request → Redis → response
cycle, focusing on HTTP-level behaviour: miss/hit headers, no-store bypass,
no-cache refresh, ETag 304, max-age, and private directive.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
import redis as sync_redis
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from redis_fastapi.cache import cache
from redis_fastapi.setup import FastAPIRedis
from tests.conftest import requires_redis

_call_count: int = 0


def _build_app() -> FastAPI:
    app = FastAPI()
    FastAPIRedis(app).lifespan().caching()

    @app.get("/items", dependencies=[Depends(cache(ttl=300))])
    async def get_items() -> dict:
        global _call_count
        _call_count += 1
        return {"value": _call_count}

    return app


@pytest.fixture()
def integ_client(
    real_redis: sync_redis.Redis,
) -> Generator[TestClient, None, None]:
    global _call_count
    _call_count = 0
    app = _build_app()
    with TestClient(app) as c:
        yield c
    real_redis.flushdb()


@requires_redis
@pytest.mark.integration
class TestCacheE2EMissHit:
    def test_miss_then_hit(self, integ_client: TestClient) -> None:
        r1 = integ_client.get("/items")
        assert r1.status_code == 200
        assert r1.headers["X-Redis-Cache"] == "MISS"
        val = r1.json()["value"]

        r2 = integ_client.get("/items")
        assert r2.status_code == 200
        assert r2.headers["X-Redis-Cache"] == "HIT"
        assert r2.json()["value"] == val

    def test_headers_present(self, integ_client: TestClient) -> None:
        r = integ_client.get("/items")
        assert "max-age=" in r.headers["Cache-Control"]
        assert r.headers["ETag"].startswith('W/"')


@requires_redis
@pytest.mark.integration
class TestCacheE2ENoStore:
    def test_no_store_bypass(self, integ_client: TestClient) -> None:
        r1 = integ_client.get("/items")
        assert r1.headers["X-Redis-Cache"] == "MISS"

        r2 = integ_client.get("/items", headers={"Cache-Control": "no-store"})
        assert "X-Redis-Cache" not in r2.headers
        assert r2.json()["value"] == r1.json()["value"] + 1


@requires_redis
@pytest.mark.integration
class TestCacheE2ENoCache:
    def test_no_cache_refresh(self, integ_client: TestClient) -> None:
        r1 = integ_client.get("/items")
        val1 = r1.json()["value"]

        r2 = integ_client.get("/items", headers={"Cache-Control": "no-cache"})
        assert r2.headers["X-Redis-Cache"] == "MISS"
        assert r2.json()["value"] == val1 + 1


@requires_redis
@pytest.mark.integration
class TestCacheE2EETag:
    def test_304_on_etag_match(self, integ_client: TestClient) -> None:
        r1 = integ_client.get("/items")
        etag = r1.headers["ETag"]

        r2 = integ_client.get("/items", headers={"If-None-Match": etag})
        assert r2.status_code == 304


@requires_redis
@pytest.mark.integration
class TestCacheE2EMaxAge:
    """Client Cache-Control: max-age=N through real Redis."""

    def test_max_age_zero_forces_miss(self, integ_client: TestClient) -> None:
        r1 = integ_client.get("/items")
        assert r1.headers["X-Redis-Cache"] == "MISS"
        val1 = r1.json()["value"]

        r2 = integ_client.get("/items")
        assert r2.headers["X-Redis-Cache"] == "HIT"
        assert r2.json()["value"] == val1

        r3 = integ_client.get("/items", headers={"Cache-Control": "max-age=0"})
        assert r3.headers["X-Redis-Cache"] == "MISS"
        assert r3.json()["value"] == val1 + 1

    def test_large_max_age_allows_hit(self, integ_client: TestClient) -> None:
        r1 = integ_client.get("/items")
        val1 = r1.json()["value"]

        r2 = integ_client.get("/items", headers={"Cache-Control": "max-age=9999"})
        assert r2.headers["X-Redis-Cache"] == "HIT"
        assert r2.json()["value"] == val1


@requires_redis
@pytest.mark.integration
class TestCacheE2EPrivate:
    """``cache(private=True)`` through real Redis."""

    def test_private_header_on_miss_and_hit(self, real_redis: sync_redis.Redis) -> None:
        app = FastAPI()
        FastAPIRedis(app).lifespan().caching()

        @app.get("/me", dependencies=[Depends(cache(ttl=120, private=True))])
        async def my_profile() -> dict:
            return {"user": "alice"}

        with TestClient(app) as c:
            r1 = c.get("/me")
            assert r1.status_code == 200
            cc1 = r1.headers["Cache-Control"]
            assert "private" in cc1
            assert "max-age=120" in cc1

            r2 = c.get("/me")
            assert r2.status_code == 200
            cc2 = r2.headers["Cache-Control"]
            assert "private" in cc2

        real_redis.flushdb()

    def test_default_no_private(self, integ_client: TestClient) -> None:
        r = integ_client.get("/items")
        cc = r.headers["Cache-Control"]
        assert "private" not in cc
