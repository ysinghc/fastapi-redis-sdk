# Feature Roadmap for redis-fastapi

## Executive Summary

Based on competitive analysis and Redis/FastAPI-specific targeting, this document outlines recommended features for redis-fastapi that would strengthen its position as the **official Redis integration for FastAPI**.

---

## Current State (v0.1.0)

### ✅ Implemented
- Dependency injection (RedisDep, AsyncRedisDep)
- Automatic lifespan management (redis_lifespan)
- Endpoint caching with @cache decorator
- Redis Cluster support
- HTTP cache semantics (ETag, Cache-Control, 304 responses)
- Custom key builders
- Custom coders (Coder protocol)
- Comprehensive configuration (env vars + dataclass)
- Full type annotations

### ❌ Missing (vs competitors)
- **Metrics & observability** - No hit/miss tracking
- **Pattern-based cache clearing** - No SCAN-based invalidation
- **Built-in PickleCoder** - Must implement manually
- **Cache warming** - No preload mechanism
- **Distributed invalidation** - No pub/sub for multi-instance
- **Request-scoped caching** - No per-request cache
- **Conditional caching** - No predicate-based skip logic
- **Cache tags** - No tag-based invalidation

---

## Priority 1: High-Value, Redis-Specific Features

These features leverage Redis capabilities and strengthen the "official Redis integration" positioning.

### 1.1 Cache Metrics & Observability

**Problem**: No visibility into cache performance

**Solution**: Add built-in metrics tracking

```python
from redis_fastapi import cache, get_cache_stats

@app.get("/items")
@cache(ttl=60)
async def get_items():
    return {"items": [...]}

@app.get("/_metrics/cache")
async def cache_metrics():
    stats = await get_cache_stats()
    return {
        "hit_rate": stats.hit_rate,
        "hits": stats.hits,
        "misses": stats.misses,
        "total_requests": stats.total,
        "keys_count": stats.keys_count,
        "memory_used": stats.memory_bytes,
    }
```

**Implementation**:
- Store counters in Redis HASHs: `{prefix}:metrics:hits`, `{prefix}:metrics:misses`
- Use `HINCRBY` for atomic increments
- Expose `get_cache_stats()` async function
- Optional: Prometheus integration via `prometheus-client`

**Priority**: **HIGH** - Metrics are essential for production
**Effort**: Medium
**Dependencies**: None

---

### 1.2 Pattern-Based Cache Clearing

**Problem**: No way to invalidate related cache entries

**Solution**: Add cache clearing utilities leveraging Redis SCAN

```python
from redis_fastapi import cache, clear_cache, clear_cache_pattern

# Clear specific cache key
await clear_cache("/api/v1/items")

# Clear all items endpoints
await clear_cache_pattern("/api/v1/items*")

# Clear all cache
await clear_cache_pattern("*")

# Clear by namespace
await clear_cache_namespace("v2")
```

**Implementation**:
- Use `SCAN` to find matching keys (cluster-safe)
- Batch `DEL` operations
- Support glob patterns
- Return count of deleted keys

**Priority**: **HIGH** - Common production need
**Effort**: Medium
**Dependencies**: None

---

### 1.3 Redis Streams-Based Distributed Cache Invalidation

**Problem**: Multi-instance deployments can't coordinate cache invalidation

**Solution**: Use Redis Streams for pub/sub invalidation events

```python
from redis_fastapi import cache, invalidate_cache_distributed

app = FastAPI(lifespan=redis_lifespan)

@app.post("/items")
async def create_item(item: Item, redis: AsyncRedisDep):
    await redis.set(f"item:{item.id}", item.json())
    
    # Invalidate cache across all instances
    await invalidate_cache_distributed("/api/v1/items")
    
    return item

@app.get("/items")
@cache(ttl=300)  # Long TTL, invalidated on writes
async def list_items():
    return get_all_items()
```

**Implementation**:
- Use Redis Streams (`XADD`) for invalidation events
- Background task listens to stream (`XREAD`)
- Local cache cleared on event receipt
- Consumer groups for reliability

**Priority**: **MEDIUM-HIGH** - Differentiator, Redis-specific
**Effort**: High
**Dependencies**: None (Redis Streams available since Redis 5.0)

---

### 1.4 Built-in PickleCoder

**Problem**: Must implement manually (competitor has it)

**Solution**: Add secure PickleCoder implementation

```python
from redis_fastapi import cache, PickleCoder


class ComplexObject:
    # Not JSON serializable
    pass


@app.get("/complex")
@cache(ttl=60)
async def get_complex() -> ComplexObject:
    return ComplexObject()
```

**Implementation**:
- Add `PickleCoder` class in `types.py`
- Use `pickle.dumps()`/`pickle.loads()`
- Add security warning in docs (pickle is unsafe for untrusted data)
- Optional: HMAC signing for tamper detection

**Priority**: **MEDIUM** - Competitive parity
**Effort**: Low
**Dependencies**: stdlib only

---

## Priority 2: FastAPI Integration Enhancements

Features that deepen FastAPI integration beyond what competitors offer.

### 2.1 Request-Scoped Cache (Per-Request Deduplication)

**Problem**: Multiple calls to same function within a request execute repeatedly

**Solution**: Add request-scoped caching layer

```python
from redis_fastapi import cache, request_cache

@request_cache  # Caches for duration of request only
async def get_user(user_id: int) -> User:
    # Called once per request, even if invoked multiple times
    return await db.fetch_user(user_id)

@app.get("/dashboard")
async def dashboard():
    user = await get_user(1)  # DB call
    profile = await get_profile(1)  # Uses get_user(1) - no DB call
    settings = await get_settings(1)  # Uses get_user(1) - no DB call
    return {"user": user, "profile": profile, "settings": settings}
```

**Implementation**:
- Store in `request.state._cache`
- Key by function + args
- No Redis needed (in-memory)
- Decorator similar to `@cache` but scoped to request

**Priority**: **MEDIUM** - Solves N+1 query problem
**Effort**: Low
**Dependencies**: None

---

### 2.2 Conditional Caching with Predicates

**Problem**: Sometimes need to skip caching based on request context

**Solution**: Add predicate function to cache decorator

```python
from redis_fastapi import cache


def cache_if_not_admin(request: Request) -> bool:
    return request.headers.get("X-User-Role") != "admin"


@app.get("/items")
@cache(ttl=60)
async def get_items():
    # Admins always get fresh data
    return get_all_items()
```

**Implementation**:
- Add `cache_if` parameter to `@cache()`
- Callable receives `Request` → `bool`
- Check predicate before cache lookup

**Priority**: **LOW-MEDIUM** - Nice to have
**Effort**: Low
**Dependencies**: None

---

### 2.3 Cache Tags for Grouped Invalidation

**Problem**: Related cache entries hard to invalidate together

**Solution**: Tag-based invalidation system

```python
from redis_fastapi import cache, invalidate_tags


@app.get("/users/{user_id}")
@cache(ttl=300)
async def get_user(user_id: int):
    return fetch_user(user_id)


@app.get("/users/{user_id}/posts")
@cache(ttl=300)
async def get_user_posts(user_id: int):
    return fetch_posts(user_id)


@app.post("/users/{user_id}")
async def update_user(user_id: int, data: dict):
    save_user(user_id, data)
    # Invalidate all caches tagged with this user
    await invalidate_tags(f"user:{user_id}")
    return {"status": "updated"}
```

**Implementation**:
- Store tag → key mapping in Redis SETs
- `SADD {prefix}:tag:{tag} {cache_key}`
- Invalidation: `SMEMBERS` → batch `DEL`
- Auto-cleanup on TTL expiry

**Priority**: **MEDIUM** - Powerful pattern
**Effort**: Medium-High
**Dependencies**: None

---

## Priority 3: Developer Experience Improvements

### 3.1 Cache Warming Utilities

**Problem**: Cold start causes slow responses

**Solution**: Add cache warming helpers

```python
from redis_fastapi import warm_cache

async def on_startup():
    # Warm critical caches
    await warm_cache("/api/v1/items", method="GET")
    await warm_cache("/api/v1/users", method="GET", params={"page": "1"})

app = FastAPI(lifespan=redis_lifespan, on_startup=[on_startup])
```

**Implementation**:
- Build cache key from path + params
- Execute endpoint function
- Store result with TTL
- Return warming stats

**Priority**: **LOW** - Nice to have
**Effort**: Low
**Dependencies**: None

---

---

## Priority 4: Advanced Redis Features

Redis-specific advanced capabilities.

### 4.1 RedisJSON Support

**Problem**: Complex nested data structures need JSONPath queries

**Solution**: Add RedisJSON integration

```python
from redis_fastapi import JsonDep

@app.get("/config")
async def get_config(redis_json: JsonDep):
    # Store complex JSON
    await redis_json.set("app:config", ".", {"db": {"host": "localhost"}})
    
    # Query with JSONPath
    db_host = await redis_json.get("app:config", "$.db.host")
    return {"db_host": db_host}
```

**Priority**: **LOW** - Niche use case
**Effort**: Medium
**Dependencies**: RedisJSON module

---

### 4.2 RedisBloom for Probabilistic Caching

**Problem**: Cache stampede on popular keys

**Solution**: Use Bloom filter to track "in-flight" requests

```python
@cache(ttl=60, use_bloom=True)
async def expensive_operation():
    # Only one request executes, others wait
    return compute_result()
```

**Priority**: **LOW** - Advanced
**Effort**: High
**Dependencies**: RedisBloom module

---

## Recommended Implementation Order

### Phase 1 (v0.2.0) - Production Essentials
1. **Cache Metrics** (1.1) - Essential for production
2. **Pattern-Based Clearing** (1.2) - Common need
3. **Built-in PickleCoder** (1.4) - Competitive parity

**Timeline**: 1-2 months
**Value**: Makes library production-ready

### Phase 2 (v0.3.0) - Advanced Caching
4. **Distributed Invalidation** (1.3) - Differentiator
5. **Cache Tags** (2.3) - Powerful pattern
6. **Request-Scoped Cache** (2.1) - DX improvement

**Timeline**: 2-3 months
**Value**: Differentiation from competitors

### Phase 3 (v0.4.0) - Developer Experience
7. **Conditional Caching** (2.2) - Flexibility
8. **Cache Warming** (3.1) - Performance

**Timeline**: 1-2 months
**Value**: Best-in-class DX

### Phase 4 (v1.0.0+) - Advanced Redis Features
10. **RedisJSON Support** (4.1) - Redis Stack integration
11. **RedisBloom** (4.2) - Advanced patterns

**Timeline**: Future
**Value**: Showcase Redis capabilities

---

## Summary: Strategic Positioning

By implementing Phase 1-2 features, redis-fastapi will:

1. ✅ **Match competitors** on essential features (metrics, clearing, pickle)
2. ✅ **Differentiate** with Redis-specific capabilities (Streams invalidation, tags)
3. ✅ **Strengthen** "official Redis integration" positioning
4. ✅ **Maintain** focus on Redis (no multi-backend complexity)
5. ✅ **Leverage** Redis advanced features others can't (Streams, Cluster)

**Key differentiators vs fastapi-cache2**:
- Official Redis backing
- Redis Cluster support ✅ (already have)
- Distributed cache invalidation (Phase 2)
- Native Redis Streams integration (Phase 2)
- Direct Redis client access ✅ (already have)

This roadmap positions redis-fastapi as the **best FastAPI caching solution for teams committed to Redis**.
