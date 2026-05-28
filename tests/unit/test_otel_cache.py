"""Integration tests: OTel spans and metrics emitted during cache operations.

Uses OTel in-memory exporters to verify spans/metrics without a real collector.
"""

from __future__ import annotations

import fakeredis.aioredis
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

import redis_fastapi.telemetry as tel
from redis_fastapi.cache import cache, cache_evict, cache_put, default_key_builder
from redis_fastapi.cache_backend import CacheBackend
from redis_fastapi.deps import get_async_redis
from redis_fastapi.setup import FastAPIRedis

# ---------------------------------------------------------------------------
# In-memory span exporter (not shipped with all OTel SDK versions)
# ---------------------------------------------------------------------------


class InMemorySpanExporter(SpanExporter):
    """Collects finished spans in memory for test assertions."""

    def __init__(self) -> None:
        self._spans: list = []

    def export(self, spans):  # type: ignore[override]
        self._spans.extend(spans)
        return SpanExportResult.SUCCESS

    def get_finished_spans(self) -> list:
        return list(self._spans)

    def clear(self) -> None:
        self._spans.clear()

    def shutdown(self) -> None:
        pass


# ---------------------------------------------------------------------------
# OTel test fixtures - module-scoped providers (set once), per-test reset
# ---------------------------------------------------------------------------

_span_exporter = InMemorySpanExporter()
_tracer_provider = TracerProvider()
_tracer_provider.add_span_processor(SimpleSpanProcessor(_span_exporter))
trace.set_tracer_provider(_tracer_provider)

_metric_reader = InMemoryMetricReader()
_meter_provider = MeterProvider(metric_readers=[_metric_reader])
metrics.set_meter_provider(_meter_provider)


@pytest.fixture(autouse=True)
def _otel_reset_telemetry():
    """Reset telemetry module state and clear exporters before each test."""
    orig = tel._state
    tel.disable_telemetry()
    _span_exporter.clear()

    yield

    tel._state = orig


@pytest.fixture()
def span_exporter() -> InMemorySpanExporter:
    return _span_exporter


@pytest.fixture()
def metric_reader() -> InMemoryMetricReader:
    return _metric_reader


def _make_otel_app(
    fake: fakeredis.aioredis.FakeRedis,
) -> tuple[FastAPI, list[int]]:
    app = FastAPI()
    FastAPIRedis(app).caching().otel()
    counts: list[int] = [0]

    @app.get("/cached", dependencies=[Depends(cache(ttl=300))])
    async def cached_endpoint() -> dict:
        counts[0] += 1
        return {"value": counts[0]}

    @app.get(
        "/products/{product_id}",
        dependencies=[Depends(cache(ttl=300, eviction_group="products"))],
    )
    async def get_product(product_id: int) -> dict:
        counts[0] += 1
        return {"id": product_id, "v": counts[0]}

    @app.delete(
        "/products/{product_id}",
        dependencies=[
            Depends(
                cache_evict(eviction_group="products", key_builder=default_key_builder)
            )
        ],
    )
    async def delete_product(product_id: int) -> dict:
        return {"deleted": product_id}

    @app.put(
        "/products/{product_id}",
        dependencies=[
            Depends(
                cache_put(
                    eviction_group="products", key_builder=default_key_builder, ttl=300
                )
            )
        ],
    )
    async def update_product(product_id: int) -> dict:
        return {"id": product_id, "updated": True}

    @app.post(
        "/products/clear",
        dependencies=[Depends(cache_evict(eviction_group="products"))],
    )
    async def clear_products() -> dict:
        return {"ok": True}

    async def _fake() -> fakeredis.aioredis.FakeRedis:
        return fake

    app.dependency_overrides[get_async_redis] = _fake
    return app, counts


# ===================================================================
# Cache HIT / MISS spans
# ===================================================================


@pytest.mark.unit
class TestOtelCacheHitMiss:
    def test_miss_emits_span_with_hit_false(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        app, _ = _make_otel_app(fake_async_redis)
        with TestClient(app) as c:
            c.get("/cached")

        spans = span_exporter.get_finished_spans()
        cache_get_spans = [s for s in spans if s.name == "cache.get"]
        assert len(cache_get_spans) >= 1
        span = cache_get_spans[0]
        assert span.attributes.get("cache.hit") is False

    def test_hit_emits_span_with_hit_true(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        app, _ = _make_otel_app(fake_async_redis)
        with TestClient(app) as c:
            c.get("/cached")
            span_exporter.clear()
            c.get("/cached")  # HIT

        spans = span_exporter.get_finished_spans()
        cache_get_spans = [s for s in spans if s.name == "cache.get"]
        assert len(cache_get_spans) >= 1
        hit_span = cache_get_spans[0]
        assert hit_span.attributes.get("cache.hit") is True


# ===================================================================
# Cache SET span (miss fill)
# ===================================================================


@pytest.mark.unit
class TestOtelCacheSet:
    def test_miss_emits_cache_set_span(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        app, _ = _make_otel_app(fake_async_redis)
        with TestClient(app) as c:
            c.get("/cached")

        spans = span_exporter.get_finished_spans()
        cache_set_spans = [s for s in spans if s.name == "cache.set"]
        assert len(cache_set_spans) == 1
        assert cache_set_spans[0].attributes.get("cache.ttl") == 300


# ===================================================================
# Cache evict spans
# ===================================================================


@pytest.mark.unit
class TestOtelCacheEvict:
    def test_evict_with_key_emits_span(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        app, _ = _make_otel_app(fake_async_redis)
        with TestClient(app) as c:
            c.get("/products/42")
            span_exporter.clear()
            c.delete("/products/42")

        spans = span_exporter.get_finished_spans()
        evict_spans = [s for s in spans if s.name == "cache.evict"]
        assert len(evict_spans) >= 1
        assert evict_spans[0].attributes.get("cache.evict_type") == "key"

    def test_evict_group_emits_span(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        app, _ = _make_otel_app(fake_async_redis)
        with TestClient(app) as c:
            c.get("/products/42")
            span_exporter.clear()
            c.post("/products/clear")

        spans = span_exporter.get_finished_spans()
        evict_spans = [s for s in spans if s.name == "cache.evict"]
        assert len(evict_spans) >= 1
        assert evict_spans[0].attributes.get("cache.evict_type") == "group"


# ===================================================================
# Cache put span
# ===================================================================


@pytest.mark.unit
class TestOtelCachePut:
    def test_put_emits_span(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        app, _ = _make_otel_app(fake_async_redis)
        with TestClient(app) as c:
            span_exporter.clear()
            c.put("/products/42")

        spans = span_exporter.get_finished_spans()
        put_spans = [s for s in spans if s.name == "cache.put"]
        assert len(put_spans) == 1
        assert put_spans[0].attributes.get("cache.eviction_group") == "products"


# ===================================================================
# Bypass span (no-store)
# ===================================================================


@pytest.mark.unit
class TestOtelBypass:
    def test_no_store_does_not_emit_cache_get_span(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        app, _ = _make_otel_app(fake_async_redis)
        with TestClient(app) as c:
            c.get("/cached", headers={"Cache-Control": "no-store"})

        spans = span_exporter.get_finished_spans()
        cache_get_spans = [s for s in spans if s.name == "cache.get"]
        assert len(cache_get_spans) == 0


# ===================================================================
# CacheBackend spans
# ===================================================================


@pytest.mark.unit
class TestOtelCacheBackend:
    async def test_backend_get_miss_emits_span(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        tel.enable_telemetry()
        backend = CacheBackend(fake_async_redis, eviction_group="test")
        result = await backend.get("missing")
        assert result is None

        spans = span_exporter.get_finished_spans()
        get_spans = [s for s in spans if s.name == "cache.backend.get"]
        assert len(get_spans) == 1
        assert get_spans[0].attributes.get("cache.hit") is False

    async def test_backend_set_and_get_hit_emits_spans(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        tel.enable_telemetry()
        backend = CacheBackend(fake_async_redis, eviction_group="test")
        await backend.set("key1", {"data": 1}, ttl=60)
        result = await backend.get("key1")
        assert result == {"data": 1}

        spans = span_exporter.get_finished_spans()
        set_spans = [s for s in spans if s.name == "cache.backend.set"]
        assert len(set_spans) == 1
        get_spans = [s for s in spans if s.name == "cache.backend.get"]
        assert len(get_spans) == 1
        assert get_spans[0].attributes.get("cache.hit") is True

    async def test_backend_delete_emits_span(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        tel.enable_telemetry()
        backend = CacheBackend(fake_async_redis, eviction_group="test")
        await backend.set("key1", "val", ttl=60)
        await backend.delete("key1")

        spans = span_exporter.get_finished_spans()
        del_spans = [s for s in spans if s.name == "cache.backend.delete"]
        assert len(del_spans) == 1

    async def test_backend_delete_group_emits_span(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        tel.enable_telemetry()
        backend = CacheBackend(fake_async_redis, eviction_group="test")
        await backend.set("a", "1", ttl=60)
        await backend.set("b", "2", ttl=60)
        span_exporter.clear()
        count = await backend.delete_group("test")

        spans = span_exporter.get_finished_spans()
        ns_spans = [s for s in spans if s.name == "cache.backend.delete_group"]
        assert len(ns_spans) == 1
        assert ns_spans[0].attributes.get("cache.keys_deleted") == count

    async def test_backend_has_emits_span(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        span_exporter: InMemorySpanExporter,
    ) -> None:
        tel.enable_telemetry()
        backend = CacheBackend(fake_async_redis, eviction_group="test")
        await backend.set("key1", "val", ttl=60)
        result = await backend.has("key1")
        assert result is True

        spans = span_exporter.get_finished_spans()
        has_spans = [s for s in spans if s.name == "cache.backend.has"]
        assert len(has_spans) == 1


# ===================================================================
# Metrics (counters)
# ===================================================================


@pytest.mark.unit
class TestOtelMetrics:
    def test_cache_request_metrics_recorded(
        self,
        fake_async_redis: fakeredis.aioredis.FakeRedis,
        metric_reader: InMemoryMetricReader,
    ) -> None:
        app, _ = _make_otel_app(fake_async_redis)
        with TestClient(app) as c:
            c.get("/cached")  # MISS
            c.get("/cached")  # HIT

        data = metric_reader.get_metrics_data()
        metrics_by_name = {}
        for resource_metrics in data.resource_metrics:
            for scope_metrics in resource_metrics.scope_metrics:
                for metric in scope_metrics.metrics:
                    metrics_by_name[metric.name] = metric

        assert "redis_fastapi.cache.requests" in metrics_by_name
        assert "redis_fastapi.cache.writes" in metrics_by_name
        assert "redis_fastapi.cache.latency" in metrics_by_name
