"""Integration tests for cache invalidation and write-through patterns.

Tests eviction (single key, multi-key, targeted), write-through (put),
conditional caching, cascade invalidation, and collection+item invalidation,
all using CacheBackend DI.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest
import redis as sync_redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from redis_fastapi import CacheBackendDep, FastAPIRedis
from tests.conftest import requires_redis

_counters: dict[str, int] = {}


def _increment(key: str) -> int:
    _counters[key] = _counters.get(key, 0) + 1
    return _counters[key]


class Product(BaseModel):
    id: int
    name: str
    price: float


class UserProfile(BaseModel):
    id: int
    name: str
    bio: str


def _build_app() -> FastAPI:
    app = FastAPI()
    FastAPIRedis(app).lifespan()

    @app.get("/products/{product_id}")
    async def get_product(product_id: int, cache: CacheBackendDep) -> Product:
        cached = await cache.get(f"product:{product_id}", eviction_group="products")
        if cached is not None:
            return Product(**cached)
        product = Product(
            id=product_id,
            name=f"Product {product_id}",
            price=10.0 * _increment(f"product:{product_id}"),
        )
        await cache.set(
            f"product:{product_id}",
            product.model_dump(),
            ttl=300,
            eviction_group="products",
        )
        return product

    @app.post("/products/{product_id}")
    async def invalidate_product(product_id: int, cache: CacheBackendDep) -> dict:
        _increment(f"invalidate-product:{product_id}")
        await cache.delete(f"product:{product_id}", eviction_group="products")
        return {"invalidated": product_id}

    @app.put("/products/{product_id}")
    async def replace_product(product_id: int, cache: CacheBackendDep) -> dict:
        _increment(f"put-product:{product_id}")
        result = {"id": product_id, "name": f"Updated {product_id}", "price": 999.0}
        await cache.set(
            f"product:{product_id}", result, ttl=300, eviction_group="products"
        )
        return result

    @app.get("/expensive-products/{product_id}")
    async def get_expensive_product(product_id: int, cache: CacheBackendDep) -> Product:
        cached = await cache.get(f"expensive:{product_id}", eviction_group="products")
        if cached is not None:
            return Product(**cached)
        price = 5.0 * _increment(f"expensive:{product_id}")
        product = Product(id=product_id, name=f"Product {product_id}", price=price)
        if product.price >= 10.0:
            await cache.set(
                f"expensive:{product_id}",
                product.model_dump(),
                ttl=300,
                eviction_group="products",
            )
        return product

    @app.get("/users")
    async def list_users(cache: CacheBackendDep) -> list[dict]:
        cached = await cache.get("all", eviction_group="users")
        if cached is not None:
            return cached
        users = [
            {"id": i, "name": f"User {i}"}
            for i in range(1, _increment("users:list") + 3)
        ]
        await cache.set("all", users, ttl=300, eviction_group="users")
        return users

    @app.get("/users/{user_id}")
    async def get_user(user_id: int, cache: CacheBackendDep) -> dict:
        cached = await cache.get(f"user:{user_id}", eviction_group="users")
        if cached is not None:
            return cached
        user = {
            "id": user_id,
            "name": f"User {user_id}",
            "v": _increment(f"user:{user_id}"),
        }
        await cache.set(f"user:{user_id}", user, ttl=300, eviction_group="users")
        return user

    @app.put("/users/{user_id}")
    async def update_user(user_id: int, cache: CacheBackendDep) -> dict:
        _increment(f"update-user:{user_id}")
        await cache.delete(f"user:{user_id}", eviction_group="users")
        await cache.delete("all", eviction_group="users")
        return {"updated": user_id}

    @app.get("/profile/{user_id}")
    async def get_profile(user_id: int, cache: CacheBackendDep) -> UserProfile:
        cached = await cache.get(f"profile:{user_id}", eviction_group="profiles")
        if cached is not None:
            return UserProfile(**cached)
        profile = UserProfile(
            id=user_id,
            name=f"User {user_id}",
            bio=f"Bio v{_increment(f'profile:{user_id}')}",
        )
        await cache.set(
            f"profile:{user_id}",
            profile.model_dump(),
            ttl=300,
            eviction_group="profiles",
        )
        return profile

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
        return {"user_id": user_id, "orders": orders}

    @app.put("/profile/{user_id}")
    async def update_profile(user_id: int, cache: CacheBackendDep) -> dict:
        _increment(f"update-profile:{user_id}")
        await cache.delete(f"profile:{user_id}", eviction_group="profiles")
        await cache.delete(f"orders:{user_id}", eviction_group="dashboard")
        await cache.delete("all", eviction_group="users")
        return {"ok": True}

    @app.get("/session/{session_id}")
    async def get_session(session_id: str, cache: CacheBackendDep) -> dict:
        cached = await cache.get(f"sess:{session_id}", eviction_group="sessions")
        if cached is not None:
            return cached
        session = {
            "session_id": session_id,
            "counter": _increment(f"sess:{session_id}"),
        }
        await cache.set(
            f"sess:{session_id}", session, ttl=300, eviction_group="sessions"
        )
        return session

    @app.put("/settings/{key}")
    async def set_setting(key: str, cache: CacheBackendDep) -> dict:
        value = f"custom-{key}"
        await cache.set(f"setting:{key}", value, ttl=300, eviction_group="settings")
        return {"value": value}

    return app


@pytest.fixture()
def client(real_redis: sync_redis.Redis) -> Generator[TestClient, None, None]:
    _counters.clear()
    app = _build_app()
    with TestClient(app) as c:
        yield c
    real_redis.flushdb()


# ---------------------------------------------------------------------------
# Single-key eviction
# ---------------------------------------------------------------------------


@requires_redis
@pytest.mark.integration
class TestCacheEvict:
    """DI-based invalidation on write endpoints."""

    def test_evict_single_key(self, client: TestClient) -> None:
        r1 = client.get("/products/1")
        original_price = r1.json()["price"]
        client.post("/products/1")
        r2 = client.get("/products/1")
        assert r2.json()["price"] != original_price
        assert _counters["product:1"] == 2

    def test_evict_is_targeted(self, client: TestClient) -> None:
        """Evicting product 1 should not affect product 2."""
        client.get("/products/1")
        client.get("/products/2")
        client.post("/products/1")
        client.get("/products/2")
        assert _counters["product:2"] == 1

    def test_evict_multiple_keys(self, client: TestClient) -> None:
        client.get("/users")
        client.get("/users/1")
        assert _counters["users:list"] == 1
        assert _counters["user:1"] == 1
        client.put("/users/1")
        client.get("/users")
        client.get("/users/1")
        assert _counters["users:list"] == 2
        assert _counters["user:1"] == 2

    def test_evict_does_not_affect_other_eviction_groups(
        self, client: TestClient
    ) -> None:
        client.get("/products/1")
        client.get("/session/abc")
        client.post("/products/1")
        client.get("/session/abc")
        assert _counters["sess:abc"] == 1


# ---------------------------------------------------------------------------
# Write-through (put)
# ---------------------------------------------------------------------------


@requires_redis
@pytest.mark.integration
class TestCachePut:
    """DI-based write-through caching."""

    def test_put_populates_cache_for_subsequent_get(self, client: TestClient) -> None:
        r1 = client.put("/products/1")
        assert r1.json()["price"] == 999.0
        r2 = client.get("/products/1")
        assert r2.json()["price"] == 999.0
        assert "product:1" not in _counters

    def test_put_overwrites_existing_cache(self, client: TestClient) -> None:
        r1 = client.get("/products/1")
        assert r1.json()["price"] == 10.0
        client.put("/products/1")
        r2 = client.get("/products/1")
        assert r2.json()["price"] == 999.0
        assert _counters["product:1"] == 1

    def test_put_result_returned_to_caller(self, client: TestClient) -> None:
        r = client.put("/products/99")
        assert r.json() == {"id": 99, "name": "Updated 99", "price": 999.0}

    def test_put_feeds_di_cache_read(self, client: TestClient) -> None:
        client.put("/settings/theme")
        client.put("/products/1")
        r2 = client.get("/products/1")
        assert r2.json()["price"] == 999.0


# ---------------------------------------------------------------------------
# Conditional caching
# ---------------------------------------------------------------------------


@requires_redis
@pytest.mark.integration
class TestConditionalCaching:
    """Only cache when business rules are met."""

    def test_cheap_product_not_cached(self, client: TestClient) -> None:
        """Products with price < 10 should NOT be cached."""
        r1 = client.get("/expensive-products/1")
        assert r1.json()["price"] == 5.0
        r2 = client.get("/expensive-products/1")
        assert r2.json()["price"] == 10.0
        assert _counters["expensive:1"] == 2

    def test_expensive_product_is_cached(self, client: TestClient) -> None:
        """Once price >= 10 it should be cached on subsequent calls."""
        client.get("/expensive-products/1")
        client.get("/expensive-products/1")
        assert _counters["expensive:1"] == 2
        r3 = client.get("/expensive-products/1")
        assert r3.json()["price"] == 10.0
        assert _counters["expensive:1"] == 2


# ---------------------------------------------------------------------------
# Cascade invalidation across eviction groups
# ---------------------------------------------------------------------------


@requires_redis
@pytest.mark.integration
class TestCascadeInvalidation:
    """One write invalidates caches across multiple eviction groups."""

    def test_profile_update_cascades(self, client: TestClient) -> None:
        client.get("/profile/1")
        client.get("/dashboard/1")
        client.get("/users")
        assert _counters["profile:1"] == 1
        assert _counters["orders:1"] == 1
        assert _counters["users:list"] == 1

        client.put("/profile/1")

        r = client.get("/profile/1")
        assert r.json()["bio"] == "Bio v2"
        assert _counters["profile:1"] == 2
        client.get("/dashboard/1")
        assert _counters["orders:1"] == 2
        client.get("/users")
        assert _counters["users:list"] == 2

    def test_cascade_does_not_affect_unrelated_caches(self, client: TestClient) -> None:
        client.get("/products/1")
        client.get("/session/abc")
        client.get("/profile/1")
        client.put("/profile/1")
        client.get("/products/1")
        assert _counters["product:1"] == 1
        client.get("/session/abc")
        assert _counters["sess:abc"] == 1


# ---------------------------------------------------------------------------
# Collection + item invalidation
# ---------------------------------------------------------------------------


@requires_redis
@pytest.mark.integration
class TestCollectionItemInvalidation:
    """DI read + DI write for collections."""

    def test_list_and_item_cached_independently(self, client: TestClient) -> None:
        users = client.get("/users").json()
        assert len(users) == 3
        user1 = client.get("/users/1").json()
        assert user1["name"] == "User 1"
        assert client.get("/users").json() == users
        assert client.get("/users/1").json() == user1
        assert _counters["users:list"] == 1
        assert _counters["user:1"] == 1

    def test_item_update_invalidates_both_list_and_item(
        self, client: TestClient
    ) -> None:
        client.get("/users")
        client.get("/users/1")
        client.put("/users/1")
        client.get("/users/1")
        assert _counters["user:1"] == 2
        client.get("/users")
        assert _counters["users:list"] == 2

    def test_item_update_does_not_affect_other_items(self, client: TestClient) -> None:
        client.get("/users/1")
        client.get("/users/2")
        client.put("/users/1")
        client.get("/users/2")
        assert _counters["user:2"] == 1
