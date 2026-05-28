"""Integration tests for advanced caching patterns.

Tests intermediate result caching, dynamic TTL based on data, and atomic
read-modify-write -- patterns that require CacheBackend DI because they
cannot be expressed with simple dependency factories.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
import redis as sync_redis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from redis_fastapi import CacheBackendDep, FastAPIRedis
from tests.conftest import requires_redis

_counters: dict[str, int] = {}


def _increment(key: str) -> int:
    _counters[key] = _counters.get(key, 0) + 1
    return _counters[key]


def _build_app() -> FastAPI:
    app = FastAPI()
    FastAPIRedis(app).lifespan()

    # ---- Intermediate result caching ----

    @app.get("/dashboard/{user_id}")
    async def get_dashboard(user_id: int, cache: CacheBackendDep) -> dict:
        order_key = f"orders:{user_id}"
        orders = await cache.get(order_key, eviction_group="dashboard")
        if orders is None:
            orders = {
                "total_orders": _increment(f"orders:{user_id}"),
                "total_spent": 99.99,
            }
            await cache.set(order_key, orders, ttl=60, eviction_group="dashboard")

        reco_key = f"reco:{user_id}"
        recommendations = await cache.get(reco_key, eviction_group="dashboard")
        if recommendations is None:
            recommendations = [
                f"item-{i}" for i in range(_increment(f"reco:{user_id}"), 0, -1)
            ]
            await cache.set(
                reco_key, recommendations, ttl=120, eviction_group="dashboard"
            )

        return {
            "user_id": user_id,
            "orders": orders,
            "recommendations": recommendations,
        }

    @app.post("/orders/{user_id}")
    async def create_order(user_id: int, cache: CacheBackendDep) -> dict:
        _increment(f"create-order:{user_id}")
        await cache.delete(f"orders:{user_id}", eviction_group="dashboard")
        return {"created": user_id}

    # ---- Dynamic TTL based on data ----

    @app.get("/content/{content_id}")
    async def get_content(content_id: int, cache: CacheBackendDep) -> dict:
        cached = await cache.get(f"content:{content_id}", eviction_group="content")
        if cached is not None:
            return cached
        premium = content_id % 2 == 0
        content = {
            "id": content_id,
            "body": f"Content {content_id}",
            "premium": premium,
            "v": _increment(f"content:{content_id}"),
        }
        ttl = 3600 if premium else 300
        await cache.set(
            f"content:{content_id}", content, ttl=ttl, eviction_group="content"
        )
        return content

    # ---- Atomic read-modify-write ----

    @app.post("/products/{product_id}/view")
    async def record_view(product_id: int, cache: CacheBackendDep) -> dict:
        views = await cache.get(
            f"views:{product_id}", default=0, eviction_group="analytics"
        )
        views += 1
        await cache.set(
            f"views:{product_id}", views, ttl=3600, eviction_group="analytics"
        )
        return {"product_id": product_id, "views": views}

    return app


@pytest.fixture()
def client(real_redis: sync_redis.Redis) -> Generator[TestClient, None, None]:
    _counters.clear()
    app = _build_app()
    with TestClient(app) as c:
        yield c
    real_redis.flushdb()


# ---------------------------------------------------------------------------
# Intermediate result caching
# ---------------------------------------------------------------------------


@requires_redis
@pytest.mark.integration
class TestIntermediateResultCaching:
    """Cache sub-computations assembled into a response."""

    def test_dashboard_caches_intermediate_results(self, client: TestClient) -> None:
        r1 = client.get("/dashboard/1")
        body = r1.json()
        assert body["orders"]["total_orders"] == 1
        assert body["orders"]["total_spent"] == 99.99
        assert len(body["recommendations"]) == 1

        r2 = client.get("/dashboard/1")
        assert r2.json() == body
        assert _counters["orders:1"] == 1
        assert _counters["reco:1"] == 1

    def test_partial_invalidation(self, client: TestClient) -> None:
        """Invalidating orders leaves recommendations cached."""
        client.get("/dashboard/1")
        assert _counters["orders:1"] == 1
        assert _counters["reco:1"] == 1

        client.post("/orders/1")

        client.get("/dashboard/1")
        assert _counters["orders:1"] == 2
        assert _counters["reco:1"] == 1


# ---------------------------------------------------------------------------
# Dynamic TTL
# ---------------------------------------------------------------------------


@requires_redis
@pytest.mark.integration
class TestDynamicTTL:
    """TTL varies based on cached data (cannot be expressed with DI factories)."""

    def test_premium_content_cached(self, client: TestClient) -> None:
        r1 = client.get("/content/2")
        assert r1.json()["premium"] is True
        r2 = client.get("/content/2")
        assert r2.json() == r1.json()
        assert _counters["content:2"] == 1

    def test_regular_content_cached(self, client: TestClient) -> None:
        r1 = client.get("/content/3")
        assert r1.json()["premium"] is False
        r2 = client.get("/content/3")
        assert r2.json() == r1.json()
        assert _counters["content:3"] == 1

    def test_premium_and_regular_have_different_ttl(self, client: TestClient) -> None:
        client.get("/content/2")  # premium (TTL=3600)
        client.get("/content/3")  # regular (TTL=300)

        r = sync_redis.Redis(decode_responses=True)
        premium_keys = r.keys("*content:2*")
        regular_keys = r.keys("*content:3*")

        if premium_keys and regular_keys:
            premium_ttl = r.ttl(premium_keys[0])
            regular_ttl = r.ttl(regular_keys[0])
            assert premium_ttl > regular_ttl


# ---------------------------------------------------------------------------
# Atomic read-modify-write
# ---------------------------------------------------------------------------


@requires_redis
@pytest.mark.integration
class TestAtomicReadModifyWrite:
    """Read cached value, modify it, write back (requires DI)."""

    def test_view_counter_increments(self, client: TestClient) -> None:
        r1 = client.post("/products/1/view")
        assert r1.json() == {"product_id": 1, "views": 1}
        r2 = client.post("/products/1/view")
        assert r2.json() == {"product_id": 1, "views": 2}
        r3 = client.post("/products/1/view")
        assert r3.json() == {"product_id": 1, "views": 3}

    def test_view_counters_are_per_product(self, client: TestClient) -> None:
        client.post("/products/1/view")
        client.post("/products/1/view")
        client.post("/products/2/view")

        r1 = client.post("/products/1/view")
        assert r1.json()["views"] == 3
        r2 = client.post("/products/2/view")
        assert r2.json()["views"] == 2
