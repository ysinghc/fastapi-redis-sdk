# Complete Pattern Comparison Table

✅ = built-in &nbsp; 🔧 = possible with manual code &nbsp; ❌ = not applicable

## Quick Reference

| Feature | `cache()` | `cache_evict()` / `cache_put()` | CacheBackend |
|---------|:---------:|:-------------------------------:|:------------:|
| **Performance (Cache HIT)** | ~0.7 ms | N/A (write path) | ~0.1 ms per op |
| **Ease of Use** | Easy | Easy | Medium |
| **Type Safety** | ✅ | ✅ | ✅ |
| **Best For** | Most GET endpoints | Write invalidation | Custom control |

---

## Complete Feature Matrix

### HTTP Compliance

| Feature | `cache()` | `cache_evict()` / `cache_put()` | CacheBackend |
|---------|:---------:|:-------------------------------:|:------------:|
| 304 Not Modified | ✅ | ✅ | 🔧 manual |
| ETag generation | ✅ | ✅ | 🔧 manual |
| Cache-Control header | ✅ | ✅ | 🔧 manual |
| `private` / `public` directive | ✅ | ✅ (`cache_put` only) | 🔧 manual |
| X-Redis-Cache status header | ✅ | ✅ | 🔧 manual |
| Client `no-cache` (force refresh) | ✅ | ❌ | ❌ |
| Client `no-store` (bypass cache) | ✅ | ❌ | ❌ |
| Client `max-age` respected | ✅ | ❌ | ❌ |

### Caching Control

| Feature | `cache()` | `cache_evict()` / `cache_put()` | CacheBackend |
|---------|:---------:|:-------------------------------:|:------------:|
| Per-endpoint TTL | ✅ | ✅ | ✅ |
| Namespace support | ✅ | ✅ | ✅ |
| Key-level invalidation | ❌ | ✅ key_builder | ✅ |
| Namespace eviction | ❌ | ✅ (no key_builder) | ✅ `delete_namespace` |
| Write-through | ❌ | ✅ `cache_put()` | 🔧 manual |
| Conditional caching | ❌ | ❌ | ✅ full control |
| Custom key builder | ✅ | ✅ | ❌ (manual keys) |
| Custom key prefix | ✅ | ✅ | ❌ (set at init) |
| Custom coder (serializer) | ❌ | ❌ | ✅ |

### Testing & Debugging

| Feature | `cache()` | `cache_evict()` / `cache_put()` | CacheBackend |
|---------|:---------:|:-------------------------------:|:------------:|
| `dependency_overrides` | ✅ | ✅ | ✅ |
| No monkey-patching needed | ✅ | ✅ | ✅ |
| Cache headers visible | ✅ X-Redis-Cache | ✅ X-Redis-Cache | 🔧 manual |
| Debug logging | ✅ | ✅ | 🔧 manual |

### Data Handling

| Feature | `cache()` | `cache_evict()` / `cache_put()` | CacheBackend |
|---------|:---------:|:-------------------------------:|:------------:|
| Pydantic models | ✅ | ✅ | ✅ |
| Type safety | ✅ | ✅ | ✅ |
| `timedelta` TTL | ❌ (int seconds) | ❌ (int seconds) | ✅ |

### Error Handling

| Feature | `cache()` | `cache_evict()` / `cache_put()` | CacheBackend |
|---------|:---------:|:-------------------------------:|:------------:|
| Redis failure → graceful degradation | ✅ auto-fallback | ✅ auto-fallback | 🔧 manual |
| Corrupted cache data | ✅ auto-fallback | ✅ auto-fallback | 🔧 manual |

### Advanced Features

| Feature | `cache()` | `cache_evict()` / `cache_put()` | CacheBackend |
|---------|:---------:|:-------------------------------:|:------------:|
| Multi-key operations | ❌ | ❌ | 🔧 via Redis client |
| Cache warming | ❌ | ❌ | 🔧 via `set()` |
| Cache stampede prevention | ❌ | ❌ | 🔧 via Redis locks |
| Stale-while-revalidate | ❌ | ❌ | 🔧 custom logic |

---

## Decision Guide

### Choose **`cache()`** when:
- You want automatic GET response caching with ETag / 304 support
- You need per-endpoint TTL, namespace, and key builder configuration
- You need `dependency_overrides` for testing
- You want Cache-Control / no-cache / no-store compliance out of the box

### Choose **`cache_evict()` / `cache_put()`** when:
- You need to invalidate or update cache entries on POST / PUT / DELETE
- You want the same key builder to target the same cache key as `cache()`
- You need write-through caching (`cache_put`)

### Choose **CacheBackend** when:
- You need conditional caching based on business logic
- You need multi-step invalidation across namespaces
- You need to cache intermediate results (not HTTP responses)
- You need a custom serializer (coder)
- You need full control over when and what to cache

---

## Combining Patterns

All three can coexist in the same application:

```python
from fastapi import Depends, FastAPI
from redis_fastapi import FastAPIRedis, cache, cache_evict, default_key_builder, CacheBackendDep

app = FastAPI()
FastAPIRedis(app).lifespan().caching()


# cache(): automatic GET caching
@app.get("/users/{user_id}", dependencies=[Depends(cache(ttl=60, namespace="users"))])
async def get_user(user_id: int) -> User:
    return await db.get_user(user_id)


# cache_evict(): invalidate on DELETE
@app.delete(
    "/users/{user_id}",
    dependencies=[Depends(cache_evict(namespace="users", key_builder=default_key_builder))],
)
async def delete_user(user_id: int):
    await db.delete_user(user_id)


# CacheBackend: complex conditional logic
@app.post("/orders")
async def create_order(order: Order, cache_backend: CacheBackendDep):
    result = await db.create_order(order)
    await cache_backend.delete_namespace("users")
    return result
```
