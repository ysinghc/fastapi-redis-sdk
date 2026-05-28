# Architecture: Lifecycle Design

## Design Summary

The library follows FastAPI's lifespan pattern with a **shared connection pool architecture** where `redis_lifespan` creates both sync and async Redis connection pools (or cluster clients) at application startup and stores them in module-level state (`pool_state`), while dependency injection functions (`get_redis` and `get_async_redis`) retrieve clients from these shared pools for each request, falling back to creating ephemeral pools if the lifespan wasn't used. This design optimizes performance by reusing connections across requests while maintaining FastAPI's dependency injection paradigm, supports both standalone and OSS Cluster modes through runtime configuration (`get_settings().cluster`), and ensures proper cleanup by disconnecting all pools during application shutdown. The `@cache` decorator leverages `get_async_redis()` directly (not via dependency injection) to access the shared pool, while Pydantic Settings (`get_settings()`) uses `@lru_cache` to provide a singleton configuration instance that's loaded once from environment variables and `.env` files.

## Lifecycle Diagram

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'actorBkg': '#DC382D', 'actorTextColor': '#FFFFFF', 'actorBorder': '#A41E11', 'actorLineColor': '#636466', 'signalColor': '#091A23', 'signalTextColor': '#091A23', 'loopTextColor': '#091A23', 'noteBkgColor': '#F2D2CF', 'noteTextColor': '#091A23', 'noteBorderColor': '#DC382D', 'labelBoxBkgColor': '#F2D2CF', 'labelTextColor': '#091A23', 'labelBoxBorderColor': '#A41E11', 'altSectionBkgColor': '#F2D2CF', 'altSectionColor': '#091A23' }}}%%
sequenceDiagram
    participant App as FastAPI App
    participant Lifespan as redis_lifespan
    participant PoolState as pool_state<br/>(Module State)
    participant Settings as get_settings()<br/>(@lru_cache)
    participant Deps as Dependency<br/>(get_redis/get_async_redis)
    participant Cache as @cache Decorator
    participant Redis as Redis Server

    Note over App,Redis: 🚀 STARTUP PHASE

    App->>+Lifespan: startup (lifespan context manager)
    Lifespan->>Settings: get_settings()
    Settings-->>Lifespan: RedisSettings (cached)
    
    alt Cluster Mode (settings.cluster=True)
        Lifespan->>Redis: Create sync RedisCluster
        Lifespan->>Redis: Create async RedisCluster
        Lifespan->>PoolState: store sync_cluster, async_cluster
    else Standalone Mode (settings.cluster=False)
        Lifespan->>Redis: Create sync ConnectionPool
        Lifespan->>Redis: Create async ConnectionPool
        Lifespan->>PoolState: store sync_pool, async_pool
    end
    
    Lifespan-->>-App: pools ready ✅

    Note over App,Redis: 📨 REQUEST HANDLING PHASE

    loop For each request
        App->>Deps: Depends(get_redis) or Depends(get_async_redis)
        Deps->>Settings: get_settings()
        Settings-->>Deps: RedisSettings (cached)
        Deps->>PoolState: retrieve pool/cluster
        
        alt Pool exists
            PoolState-->>Deps: shared pool ✅
            Deps->>Redis: create client from shared pool
        else Pool not initialized (lifespan not used)
            Deps->>Redis: create ephemeral pool ⚠️
            Note over Deps: Fallback for compatibility
        end
        
        Deps-->>App: Redis/AsyncRedis client
        App->>Redis: execute commands (GET, SET, etc.)
        Redis-->>App: response
        Deps->>Deps: close client (pool connection returned)
    end

    Note over App,Cache: 💾 CACHING FLOW

    App->>Cache: @cache decorator on endpoint
    Cache->>Settings: get_settings()
    Settings-->>Cache: RedisSettings (TTL, prefix)
    Cache->>Deps: call get_async_redis() directly
    Deps->>PoolState: retrieve async_pool/async_cluster
    Deps-->>Cache: AsyncRedis client
    Cache->>Redis: GET cache_key
    
    alt Cache HIT
        Redis-->>Cache: cached data
        Cache-->>App: return cached response ⚡
    else Cache MISS
        Cache->>App: call original endpoint function
        App-->>Cache: fresh response
        Cache->>Redis: SET cache_key (with TTL)
        Cache-->>App: return response 💾
    end

    Note over App,Redis: 🛑 SHUTDOWN PHASE

    App->>+Lifespan: shutdown (lifespan finally block)
    Lifespan->>PoolState: retrieve pools/clusters
    
    alt Cluster Mode
        Lifespan->>Redis: sync_cluster.close()
        Lifespan->>Redis: await async_cluster.aclose()
    else Standalone Mode
        Lifespan->>Redis: sync_pool.disconnect()
        Lifespan->>Redis: await async_pool.aclose()
    end
    
    Lifespan->>PoolState: clear pool_state (set to None)
    Lifespan-->>-App: cleanup complete ✅
```

## Key Design Decisions

### 1. Shared Pool Architecture
- **Why**: Reuse connections across requests for better performance
- **How**: Module-level `pool_state` stores pools/clusters
- **Tradeoff**: Global state vs. performance (performance wins)

### 2. Lifespan Pattern
- **Why**: FastAPI's recommended way to manage startup/shutdown
- **How**: `@asynccontextmanager` creates pools on startup, destroys on shutdown
- **Reference**: [FastAPI Lifespan Events](https://fastapi.tiangolo.com/advanced/events/#lifespan)

### 3. Dependency Injection
- **Why**: FastAPI-native pattern, easy testing via `app.dependency_overrides`
- **How**: `Depends(get_redis)` and `Depends(get_async_redis)`
- **Reference**: [FastAPI Dependencies](https://fastapi.tiangolo.com/tutorial/dependencies/)

### 4. Fallback to Ephemeral Pools
- **Why**: Works without lifespan (backwards compatibility, convenience)
- **How**: `get_redis()` checks `pool_state`, creates new pool if None
- **Tradeoff**: Convenience vs. performance warning in docs

### 5. Pydantic Settings with @lru_cache
- **Why**: Singleton configuration, reads env vars once
- **How**: `@lru_cache` on `get_settings()` returns same instance
- **Reference**: [FastAPI Settings](https://fastapi.tiangolo.com/advanced/settings/)

### 6. Cache Decorator Uses Direct get_async_redis()
- **Why**: Decorators execute at import time, can't use `Depends()`
- **How**: Calls `get_async_redis()` directly in async wrapper
- **Tradeoff**: Not true dependency injection, but works with shared pool

## Telemetry

redis-fastapi supports [OpenTelemetry](https://opentelemetry.io/) for
observability.  Instrumentation is split into three independent layers:

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#DC382D', 'primaryTextColor': '#ffffff', 'lineColor': '#636466' }}}%%
block-beta
    columns 1
    block:L1:1
        columns 2
        l1["Layer 1 — HTTP request spans"]
        l1src["opentelemetry-instrumentation-fastapi"]
    end
    block:L2:1
        columns 2
        l2["Layer 2 — Cache operation spans + metrics"]
        l2src["redis-fastapi (.otel())"]
    end
    block:L3:1
        columns 2
        l3["Layer 3 — Redis command spans + connection metrics"]
        l3src["redis-py native OTel"]
    end

    style L1 fill:#636466,color:#ffffff,stroke:#636466
    style L2 fill:#DC382D,color:#ffffff,stroke:#DC382D
    style L3 fill:#A41E11,color:#ffffff,stroke:#A41E11
    style l1 fill:#636466,color:#ffffff,stroke:none
    style l1src fill:#636466,color:#ffffff,stroke:none
    style l2 fill:#DC382D,color:#ffffff,stroke:none
    style l2src fill:#DC382D,color:#ffffff,stroke:none
    style l3 fill:#A41E11,color:#ffffff,stroke:none
    style l3src fill:#A41E11,color:#ffffff,stroke:none
```

Each layer can be enabled independently.  When all three are active, a single
request produces a nested trace:

```
HTTP GET /products/42           ← Layer 1
 └── cache.get (HIT)            ← Layer 2
      └── redis GET             ← Layer 3
```

### Layer 1 — HTTP requests

Handled by the standard
[FastAPI OTel instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html).
Install `opentelemetry-instrumentation-fastapi` and call
`FastAPIInstrumentor.instrument_app(app)`.

### Layer 2 — Cache operations

This is what redis-fastapi adds.  Enable with the builder or an environment
variable:

```python
FastAPIRedis(app).lifespan().caching().otel()   # builder
```

```bash
export REDIS_OTEL_ENABLED=true           # env var
```

Requires `pip install redis-fastapi[otel]`.

**Spans** — one per cache operation:

| Span | Source |
|------|--------|
| `cache.get` | `cache()` dependency (attributes: `cache.hit`, `cache.key`, `cache.namespace`, `cache.ttl`) |
| `cache.set` | Capture middleware after a cache miss |
| `cache.evict` | `cache_evict()` dependency |
| `cache.put` | `cache_put()` dependency |
| `cache.backend.*` | `CacheBackend` methods (`get`, `set`, `delete`, `delete_namespace`, `has`) |

**Metrics:**

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `redis_fastapi.cache.requests` | Counter | `result` (`hit` / `miss` / `bypass`), `namespace` | Total cache lookups |
| `redis_fastapi.cache.writes` | Counter | `type` (`miss_fill` / `write_through`), `namespace` | Cache writes |
| `redis_fastapi.cache.evictions` | Counter | `type` (`key` / `namespace`), `namespace` | Cache invalidations |
| `redis_fastapi.cache.latency` | Histogram | `operation` (`get` / `set` / `evict`), `namespace` | Operation duration in seconds |

### Layer 3 — Redis commands

Instruments every `GET`, `SET`, `DEL`, etc. at the driver level.  Enable via:

```bash
export REDIS_OTEL_REDIS_ENABLED=true
```

Or use `opentelemetry-instrumentation-redis` externally — but not both at
once, to avoid duplicate spans.

For full configuration details (all env vars, non-intrusiveness guarantee),
see the [Configuration guide — OpenTelemetry](../guide/configuration.md#opentelemetry).

## Further Reading

- **Lifespan Management**: [`src/redis_fastapi/lifespan.py`](../../src/redis_fastapi/lifespan.py)
- **Dependency Providers**: [`src/redis_fastapi/deps.py`](../../src/redis_fastapi/deps.py)
- **Configuration**: [`docs/guide/configuration.md`](../guide/configuration.md)
- **Caching Internals**: [`src/redis_fastapi/cache.py`](../../src/redis_fastapi/cache.py)
- **FastAPI Lifespan**: https://fastapi.tiangolo.com/advanced/events/#lifespan
- **FastAPI Dependencies**: https://fastapi.tiangolo.com/tutorial/dependencies/
- **FastAPI Settings**: https://fastapi.tiangolo.com/advanced/settings/
