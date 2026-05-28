# OpenTelemetry Design for redis-fastapi

## Research Summary

| Driver | Approach | Scope |
|---|---|---|
| **redis-py (native)** | Built-in `OTelConfig` + `MetricGroup` flags. Metrics only (connection, command duration, CSC, pub/sub). Non-intrusive — errors in metric recording never break operations. | Low-level Redis command metrics |
| **redis-py (external)** | `opentelemetry-instrumentation-redis` monkey-patches clients to create **spans** per Redis command. | Tracing (spans per command) |
| **cashews** | Middleware pattern — `async def middleware(call, cmd, backend, *args, **kwargs)`. Prometheus contrib uses this to record histograms + hit/miss counters per operation. | Cache-layer metrics (hit/miss, latency per op) |
| **fastapi-cache2** | No OTel support at all. | — |
| **FastAPI (OTel instrumentation)** | `FastAPIInstrumentor` wraps the ASGI app, creates spans per HTTP request with method/route/status attributes. Supports request/response hooks. | HTTP request tracing |

### Key Insight — Three Layers

```
┌─────────────────────────────────┐
│  Layer 1: HTTP request spans    │  ← FastAPIInstrumentor (already exists)
├─────────────────────────────────┤
│  Layer 2: Cache operation spans │  ← THIS IS THE GAP redis-fastapi fills
│           + cache metrics       │
├─────────────────────────────────┤
│  Layer 3: Redis command spans   │  ← redis-py OTel (already exists)
│           + connection metrics  │
└─────────────────────────────────┘
```

redis-fastapi owns **Layer 2** — cache-level observability — and **composes** with Layers 1 and 3.

---

## 1. Configuration

Extend `RedisSettings` with two env-var-driven flags:

```python
class RedisSettings(BaseSettings):
    otel_enabled: bool = Field(
        default=False,
        description="Enable OpenTelemetry instrumentation for cache operations",
    )
    otel_redis_enabled: bool = Field(
        default=False,
        description="Also initialize redis-py native OTel (connection/command metrics)",
    )
```

Env vars: `REDIS_OTEL_ENABLED=true`, `REDIS_OTEL_REDIS_ENABLED=true`.

---

## 2. Cache-Layer Tracing (Spans)

Tracer name: `"redis-fastapi"`

| Span name | Created by | Key attributes |
|---|---|---|
| `cache.get` | `cache()` dependency | `cache.hit`, `cache.key`, `cache.namespace`, `cache.ttl` |
| `cache.set` | Capture middleware | `cache.key`, `cache.ttl` |
| `cache.evict` | `cache_evict()` | `cache.key` or `cache.namespace`, `cache.evict_type` |
| `cache.put` | `cache_put()` middleware capture | `cache.key`, `cache.ttl` |
| `cache.backend.get` | `CacheBackend.get()` | `cache.key`, `cache.hit`, `cache.namespace` |
| `cache.backend.set` | `CacheBackend.set()` | `cache.key`, `cache.ttl`, `cache.namespace` |
| `cache.backend.delete` | `CacheBackend.delete()` | `cache.key`, `cache.namespace` |
| `cache.backend.delete_namespace` | `CacheBackend.delete_namespace()` | `cache.namespace`, `cache.keys_deleted` |

---

## 3. Cache-Layer Metrics

| Metric name | Type | Labels | Description |
|---|---|---|---|
| `redis_fastapi.cache.requests` | Counter | `result` (`hit`/`miss`/`bypass`), `namespace` | Total cache lookups |
| `redis_fastapi.cache.evictions` | Counter | `type` (`key`/`namespace`), `namespace` | Cache invalidations |
| `redis_fastapi.cache.writes` | Counter | `type` (`miss_fill`/`write_through`), `namespace` | Cache writes |
| `redis_fastapi.cache.latency` | Histogram | `operation` (`get`/`set`/`evict`), `namespace` | Cache operation duration |

---

## 4. redis-py Native OTel Passthrough

When `otel_redis_enabled=true`, the lifespan initializes redis-py's native OTel on startup and shuts it down on teardown. Users who manage redis-py OTel externally leave this `false`.

---

## 5. Builder API

```python
app = FastAPI()
FastAPIRedis(app).lifespan().caching().otel()
```

---

## 6. Optional Dependency

```toml
[project.optional-dependencies]
otel = ["opentelemetry-api>=1.20", "opentelemetry-sdk>=1.20"]
```

Install: `pip install redis-fastapi[otel]`

All OTel imports are guarded with try/except. When OTel is not installed, spans and metrics are no-ops.

---

## 7. Non-Intrusiveness Guarantee

Every telemetry call is wrapped in try/except. A failure in OTel never breaks a cache operation or HTTP response. This matches redis-py and cashews.

---

## 8. Trace Visualization

When all three layers are active:

```
HTTP GET /products/42           ← FastAPIInstrumentor span
 └── cache.get                  ← redis-fastapi span (HIT or MISS)
      └── redis GET             ← redis-py span
 └── (endpoint logic)           ← only on MISS
      └── cache.set             ← redis-fastapi span (miss fill)
           └── redis SETEX      ← redis-py span
```

---

## 9. Out of Scope

| Item | Reason |
|---|---|
| HTTP-level instrumentation | Solved by `opentelemetry-instrumentation-fastapi` |
| Redis command-level spans | Solved by `opentelemetry-instrumentation-redis` / redis-py native OTel |
| Prometheus metrics directly | Bridge via `opentelemetry-exporter-prometheus` |

---

## 10. Files Changed

| File | Change |
|---|---|
| `pyproject.toml` | Add `[otel]` extras group |
| `config.py` | Add `otel_enabled`, `otel_redis_enabled` fields |
| **`telemetry.py`** (new) | `cache_span()`, meter setup, `enable_telemetry()`, metric instruments |
| `cache.py` | Wrap `cache()`, `cache_evict()`, `cache_put()` with `cache_span()` + metrics |
| `cache_backend.py` | Wrap `get`/`set`/`delete`/`delete_namespace`/`has` with `cache_span()` |
| `lifespan.py` | Init/shutdown redis-py native OTel when `otel_redis_enabled=true` |
| `setup.py` | Add `.otel()` builder method |
| `__init__.py` | Export new public symbols |
