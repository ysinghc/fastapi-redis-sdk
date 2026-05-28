# Cache Metrics & Observability - Specification

## Overview

**Feature**: Cache Metrics & Observability  
**Priority**: Critical (v0.2.0)  
**Effort**: Medium (1-2 weeks)  
**Status**: Draft Specification

## Problem Statement

Currently, redis-fastapi provides no visibility into cache performance. Production teams need to:

- Monitor cache hit/miss rates
- Track memory usage
- Identify cache effectiveness
- Debug cache behavior
- Detect cache stampedes or anomalies

Without metrics, teams operate blind and cannot optimize cache configuration.

## Goals

1. Provide real-time cache performance metrics
2. Enable Prometheus/monitoring integration
3. Keep overhead minimal (< 1ms per request)
4. Support both single-instance and cluster deployments
5. Allow per-namespace metrics (optional)

## Non-Goals

- Historical time-series storage (users should use Prometheus/Grafana)
- Per-key metrics (too granular, high overhead)
- Automatic alerting (external tools handle this)

---

## API Design

### Core Metrics API

```python
from redis_fastapi import get_cache_stats, CacheStats

# Get global cache statistics
stats: CacheStats = await get_cache_stats()

print(f"Hit rate: {stats.hit_rate:.2%}")
print(f"Hits: {stats.hits}")
print(f"Misses: {stats.misses}")
print(f"Total requests: {stats.total}")
print(f"Keys count: {stats.keys_count}")
print(f"Memory used: {stats.memory_bytes}")
print(f"Uptime: {stats.uptime_seconds}")
```

### CacheStats Dataclass

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class CacheStats:
    """Cache performance statistics."""
    
    # Counters
    hits: int
    misses: int
    
    # Derived metrics
    total: int  # hits + misses
    hit_rate: float  # hits / total (0.0-1.0)
    
    # Cache state
    keys_count: int  # Number of cached keys
    memory_bytes: int  # Approximate memory usage
    
    # Metadata
    uptime_seconds: float  # Time since metrics started tracking
    last_reset: datetime | None  # When counters were last reset
    namespace: str | None  # Namespace if filtered
```

### Metrics Endpoint (Optional)

```python
from redis_fastapi import get_cache_stats

@app.get("/_internal/cache/metrics")
async def cache_metrics():
    """Expose cache metrics for monitoring."""
    stats = await get_cache_stats()
    return {
        "hit_rate": stats.hit_rate,
        "hits": stats.hits,
        "misses": stats.misses,
        "total": stats.total,
        "keys_count": stats.keys_count,
        "memory_bytes": stats.memory_bytes,
        "uptime_seconds": stats.uptime_seconds,
    }
```

### Reset Metrics (Admin Operation)

```python
from redis_fastapi import reset_cache_stats

@app.post("/_internal/cache/metrics/reset")
async def reset_metrics():
    """Reset cache counters (admin only)."""
    await reset_cache_stats()
    return {"status": "reset"}
```

### Per-Namespace Metrics (Optional)

```python
# Get metrics for specific namespace
stats = await get_cache_stats(namespace="v2")

# List all namespaces with metrics
namespaces = await get_cache_namespaces()
# → ["", "v2", "admin"]
```

---

## Implementation Details

### Storage Structure

**Redis HASH for counters**:

```
Key: {prefix}:metrics
Fields:
  - hits: 1234567
  - misses: 234567
  - start_time: 1704067200.0  (Unix timestamp)
  - last_reset: 1704153600.0
```

**Optional: Per-namespace counters**:

```
Key: {prefix}:metrics:ns:{namespace}
Fields: (same as above)
```

### Increment Operations

Use `HINCRBY` for atomic increments:

```python
async def _increment_hit(redis: AsyncRedis, namespace: str = ""):
    """Atomically increment hit counter."""
    if namespace:
        key = f"{settings.pattern_prefix('metrics')}:ns:{namespace}"
    else:
        key = settings.pattern_prefix("metrics")
    await redis.hincrby(key, "hits", 1)
```

### Cache Decorator Integration

Modify `cache.py` to track hits/misses:

```python
async def _handle_async(func, ...):
    # ... existing cache lookup code ...
    
    if cached_value is not None:
        # Cache hit
        await _increment_hit(redis, namespace)
        # ... return cached value ...
    else:
        # Cache miss
        await _increment_miss(redis, namespace)
        # ... execute function and cache result ...
```

### Memory Estimation

Use Redis `MEMORY USAGE` command for accurate measurement:

```python
async def _get_memory_usage(redis: AsyncRedis) -> int:
    """Estimate total cache memory usage."""
    pattern = f"{settings.pattern_prefix('cache')}:*"
    
    total_bytes = 0
    async for key in redis.scan_iter(match=pattern, count=100):
        try:
            memory = await redis.memory_usage(key)
            if memory:
                total_bytes += memory
        except Exception:
            # Key expired during scan, skip
            continue
    
    return total_bytes
```

**Note**: Memory calculation is expensive (O(N) keys). Cache this value:

```python
# Refresh memory stats every 60 seconds
_memory_cache: int | None = None
_memory_cache_time: float = 0

async def _get_cached_memory_usage(redis: AsyncRedis) -> int:
    global _memory_cache, _memory_cache_time
    now = time.time()
    
    if _memory_cache is None or (now - _memory_cache_time) > 60:
        _memory_cache = await _get_memory_usage(redis)
        _memory_cache_time = now
    
    return _memory_cache
```

### Key Count

Use `SCAN` to count cache keys:

```python
async def _get_keys_count(redis: AsyncRedis) -> int:
    """Count total cached keys."""
    pattern = f"{settings.pattern_prefix('cache')}:*"
    count = 0
    async for _ in redis.scan_iter(match=pattern, count=1000):
        count += 1
    return count
```

**Optimization**: Cache this value too (expensive on large datasets).

---

## Configuration

Add to `RedisSettings`:

```python
@dataclass
class RedisSettings:
    # ... existing fields ...
    
    # Metrics
    enable_metrics: bool = True  # Track cache metrics
    metrics_per_namespace: bool = False  # Track per-namespace metrics
    metrics_memory_cache_ttl: int = 60  # Cache memory stats (seconds)
```

Environment variables:

```bash
export REDIS_ENABLE_METRICS=true
export REDIS_METRICS_PER_NAMESPACE=false
export REDIS_METRICS_MEMORY_CACHE_TTL=60
```

---

## Performance Considerations

### Overhead

Each cache operation adds:
- **Hit/Miss tracking**: 1 `HINCRBY` command (~0.1ms)
- **Memory calculation**: Cached, amortized cost negligible
- **Key counting**: Cached, amortized cost negligible

**Total overhead**: < 0.2ms per request

### Cluster Support

In Redis Cluster mode:
- Metrics HASH must be stored on specific node (hash tag)
- Use `{metrics}` hash tag to ensure single-node storage
- Example: `{prefix}:{metrics}` → `redis:fastapi:{metrics}`

```python
def _metrics_key(namespace: str = "") -> str:
    """Build metrics key with cluster hash tag."""
    if namespace:
        return f"{{{settings.prefix}}}:metrics:ns:{namespace}"
    return f"{{{settings.prefix}}}:metrics"
```

### Disable Metrics

Allow disabling for ultra-low-latency scenarios:

```python
if settings.enable_metrics:
    await _increment_hit(redis, namespace)
```

---

## Testing Strategy

### Unit Tests

```python
@pytest.mark.unit
async def test_increment_hit():
    """Test hit counter increment."""
    redis = FakeRedis()
    await _increment_hit(redis)
    stats = await get_cache_stats()
    assert stats.hits == 1
    assert stats.misses == 0

@pytest.mark.unit
async def test_hit_rate_calculation():
    """Test hit rate formula."""
    # 80 hits, 20 misses → 80% hit rate
    stats = CacheStats(hits=80, misses=20, ...)
    assert stats.hit_rate == 0.8
```

### Integration Tests

```python
@pytest.mark.integration
async def test_cache_metrics_tracking(client):
    """Test end-to-end metrics tracking."""
    # Reset metrics
    await reset_cache_stats()
    
    # First request (miss)
    resp1 = await client.get("/cached-endpoint")
    assert resp1.headers["X-Redis-Cache"] == "MISS"
    
    # Second request (hit)
    resp2 = await client.get("/cached-endpoint")
    assert resp2.headers["X-Redis-Cache"] == "HIT"
    
    # Check metrics
    stats = await get_cache_stats()
    assert stats.hits == 1
    assert stats.misses == 1
    assert stats.hit_rate == 0.5
```

---

## Documentation Requirements

1. **API Reference**: Document `get_cache_stats()`, `reset_cache_stats()`, `CacheStats`
2. **User Guide**: Add "Monitoring & Metrics" page to docs
3. **Examples**: Show Prometheus integration, Grafana dashboards
4. **Configuration**: Document all metrics-related env vars

---

## Future Enhancements (v0.3.0+)

- Prometheus exporter endpoint (`/metrics` in OpenMetrics format)
- Per-endpoint metrics (requires more storage)
- Cache stampede detection (sudden spike in misses)
- Slowlog for cache operations > threshold
- Grafana dashboard templates

---

## Acceptance Criteria

- [ ] `get_cache_stats()` returns accurate hit/miss counts
- [ ] `CacheStats` includes all specified fields
- [ ] Hit rate calculation is correct (hits / total)
- [ ] Memory usage estimation works (with caching)
- [ ] Key count works efficiently
- [ ] `reset_cache_stats()` clears all counters
- [ ] Overhead < 1ms per cached request
- [ ] Works in Redis Cluster mode
- [ ] Can be disabled via config
- [ ] Full test coverage (unit + integration)
- [ ] Documentation complete

---

## Open Questions

1. **Per-endpoint metrics?** Would require storing metrics per cache key prefix. Expensive, but valuable. → Defer to v0.3.0
2. **Percentiles (P50, P95, P99)?** Would need histogram storage. → Future enhancement
3. **Automatic alerts?** Should library handle this or leave to external tools? → External tools recommended
