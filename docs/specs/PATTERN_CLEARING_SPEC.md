# Pattern-Based Cache Clearing - Specification

## Overview

**Feature**: Pattern-Based Cache Clearing  
**Priority**: High (v0.2.0)  
**Effort**: Medium (1 week)  
**Status**: Draft Specification

## Problem Statement

Currently, there's no way to invalidate cache entries matching a pattern. Common scenarios:

- **Resource updates**: Invalidate all `/api/users/{id}/*` when user changes
- **Bulk operations**: Clear all product caches after batch import
- **Namespace invalidation**: Clear all `v2` namespace caches when deploying new version
- **Admin operations**: Clear all caches for debugging

Teams must wait for TTL expiry or restart the application, causing stale data issues.

## Goals

1. Provide pattern-based cache invalidation
2. Support glob patterns (`*`, `?`)
3. Work safely in Redis Cluster mode (SCAN, not KEYS)
4. Support namespace-based clearing
5. Return count of deleted keys for observability
6. Be non-blocking (async)

## Non-Goals

- Real-time streaming deletion (small batches are acceptable)
- Transactional guarantees (eventual consistency is fine)
- Wildcard support beyond glob patterns (no regex)

---

## API Design

### Clear Specific Cache Key

```python
from redis_fastapi import clear_cache

# Clear exact cache key
count = await clear_cache("/api/users/123")
# → Returns: 1 (number of keys deleted)
```

### Clear Pattern

```python
from redis_fastapi import clear_cache_pattern

# Clear all user endpoints
count = await clear_cache_pattern("/api/users/*")
# → Returns: 42 (deleted 42 matching keys)

# Clear all caches
count = await clear_cache_pattern("*")

# Clear with namespace
count = await clear_cache_pattern("/api/*", namespace="v2")

# Query params wildcards
count = await clear_cache_pattern("/items*")  # /items, /items?page=1, etc.
```

### Clear Namespace

```python
from redis_fastapi import clear_cache_namespace

# Clear all caches in namespace
count = await clear_cache_namespace("v2")
# → Returns: 156 (deleted all v2 namespace keys)

# Clear default namespace (no namespace)
count = await clear_cache_namespace("")
```

### Clear All

```python
from redis_fastapi import clear_all_caches

# Nuclear option: clear everything
count = await clear_all_caches()
# → Returns: 9876 (total cache keys deleted)
```

---

## Implementation Details

### Core Function: Pattern Matching

```python
async def clear_cache_pattern(
    pattern: str,
    *,
    namespace: str = "",
    prefix: str | None = None,
    batch_size: int = 100,
) -> int:
    """Clear cache keys matching a glob pattern.
    
    Args:
        pattern: Glob pattern to match (e.g., "/api/users/*")
        namespace: Namespace to filter (default: all)
        prefix: Cache prefix override (default: settings.pattern_prefix("cache"))
        batch_size: Delete keys in batches (cluster-safe)
    
    Returns:
        Number of keys deleted
    """
    redis = await get_async_redis()
    _prefix = prefix or settings.pattern_prefix("cache")
    
    # Build full Redis pattern
    parts = [_prefix]
    if namespace:
        parts.append(namespace)
    parts.append(pattern)
    redis_pattern = ":".join(parts)
    
    # Use SCAN to find matching keys (cluster-safe)
    keys_to_delete = []
    deleted_count = 0
    
    async for key in redis.scan_iter(match=redis_pattern, count=batch_size):
        keys_to_delete.append(key)
        
        # Delete in batches
        if len(keys_to_delete) >= batch_size:
            if keys_to_delete:
                deleted = await redis.delete(*keys_to_delete)
                deleted_count += deleted
                keys_to_delete = []
    
    # Delete remaining keys
    if keys_to_delete:
        deleted = await redis.delete(*keys_to_delete)
        deleted_count += deleted
    
    return deleted_count
```

### Clear Exact Key

```python
async def clear_cache(
    path: str,
    *,
    namespace: str = "",
    prefix: str | None = None,
    query_params: dict[str, str] | None = None,
) -> int:
    """Clear a specific cache key.
    
    Args:
        path: Request path (e.g., "/api/users/123")
        namespace: Namespace (default: "")
        prefix: Cache prefix override
        query_params: Query parameters dict (sorted automatically)
    
    Returns:
        1 if key deleted, 0 if not found
    """
    redis = await get_async_redis()
    _prefix = prefix or settings.pattern_prefix("cache")
    
    # Build cache key (same logic as default_key_builder)
    cache_key = _build_cache_key(path, namespace, _prefix, query_params)
    
    deleted = await redis.delete(cache_key)
    return deleted
```

### Helper: Build Cache Key

```python
def _build_cache_key(
    path: str,
    namespace: str,
    prefix: str,
    query_params: dict[str, str] | None = None,
) -> str:
    """Build cache key matching default_key_builder logic."""
    path_normalized = path.strip("/").replace("/", ":")
    parts = [prefix]
    if namespace:
        parts.append(namespace)
    if path_normalized:
        parts.append(path_normalized)
    if query_params:
        qs = ":".join(f"{k}={v}" for k, v in sorted(query_params.items()))
        parts.append(qs)
    return ":".join(parts)
```

### Clear Namespace

```python
async def clear_cache_namespace(
    namespace: str,
    *,
    prefix: str | None = None,
    batch_size: int = 100,
) -> int:
    """Clear all cache keys in a namespace.
    
    Args:
        namespace: Namespace to clear (use "" for default namespace)
        prefix: Cache prefix override
        batch_size: Delete keys in batches
    
    Returns:
        Number of keys deleted
    """
    _prefix = prefix or settings.pattern_prefix("cache")
    
    # Build pattern: {prefix}:{namespace}:*
    if namespace:
        pattern = f"{_prefix}:{namespace}:*"
    else:
        # Default namespace: {prefix}:* but exclude other namespaces
        # This requires listing all known namespaces or using SCAN carefully
        pattern = f"{_prefix}:*"
        # TODO: Filter out keys with explicit namespaces
    
    redis = await get_async_redis()
    keys_to_delete = []
    deleted_count = 0
    
    async for key in redis.scan_iter(match=pattern, count=batch_size):
        # For default namespace, skip keys with explicit namespace
        if not namespace:
            key_str = key.decode() if isinstance(key, bytes) else key
            parts = key_str.split(":")
            # Check if there's a namespace segment
            # Pattern: redis:fastapi:cache:{namespace}:{path}
            # vs:      redis:fastapi:cache:{path}
            prefix_parts = _prefix.split(":")
            if len(parts) > len(prefix_parts) + 1:
                # Might have namespace, skip for now
                # This is imperfect - better to track namespaces explicitly
                continue
        
        keys_to_delete.append(key)
        
        if len(keys_to_delete) >= batch_size:
            deleted = await redis.delete(*keys_to_delete)
            deleted_count += deleted
            keys_to_delete = []
    
    if keys_to_delete:
        deleted = await redis.delete(*keys_to_delete)
        deleted_count += deleted
    
    return deleted_count
```

### Clear All Caches

```python
async def clear_all_caches(
    *,
    prefix: str | None = None,
    batch_size: int = 100,
) -> int:
    """Clear ALL cache keys (nuclear option).
    
    Args:
        prefix: Cache prefix override
        batch_size: Delete keys in batches
    
    Returns:
        Number of keys deleted
    """
    return await clear_cache_pattern("*", prefix=prefix, batch_size=batch_size)
```

---

## Redis Cluster Considerations

### Why SCAN, not KEYS

`KEYS` pattern matching:
- ❌ Blocks Redis server (O(N) operation)
- ❌ Not cluster-safe (only searches local node)
- ✅ Simple to use

`SCAN` pattern matching:
- ✅ Non-blocking (cursor-based iteration)
- ✅ Cluster-safe (can scan all nodes)
- ❌ Slightly more complex

**Decision**: Always use `SCAN` for production safety.

### Cluster Mode Implementation

In cluster mode, must scan ALL cluster nodes:

```python
async def _scan_cluster_pattern(
    cluster: AsyncRedisCluster,
    pattern: str,
    batch_size: int = 100,
) -> AsyncIterator[str]:
    """Scan all cluster nodes for matching keys."""
    # Get all cluster nodes
    nodes = await cluster.cluster_nodes()
    
    for node in nodes.values():
        if node.get("flags") and "master" in node["flags"]:
            # Scan this master node
            node_client = cluster.get_redis_connection(node)
            async for key in node_client.scan_iter(match=pattern, count=batch_size):
                yield key
```

Update `clear_cache_pattern` to handle cluster:

```python
async def clear_cache_pattern(...) -> int:
    redis = await get_async_redis()
    
    if settings.cluster:
        # Cluster mode: scan all nodes
        async for key in _scan_cluster_pattern(redis, redis_pattern, batch_size):
            keys_to_delete.append(key)
            # ... batch deletion ...
    else:
        # Standalone mode: simple SCAN
        async for key in redis.scan_iter(match=redis_pattern, count=batch_size):
            keys_to_delete.append(key)
            # ... batch deletion ...
```

---

## Configuration

Add to `RedisSettings`:

```python
@dataclass
class RedisSettings:
    # ... existing fields ...
    
    # Cache clearing
    cache_clear_batch_size: int = 100  # Keys per batch when clearing
```

Environment variable:

```bash
export REDIS_CACHE_CLEAR_BATCH_SIZE=100
```

---

## Performance Considerations

### SCAN Performance

- **Standalone Redis**: O(N) but non-blocking, ~10,000 keys/sec
- **Redis Cluster**: O(N × nodes), slower but still non-blocking
- **Large datasets** (millions of keys): May take seconds, acceptable for admin operations

### Batch Deletion

Deleting 100 keys at once:
- `DEL key1 key2 ... key100` (single command)
- ~1-2ms per batch
- 10,000 keys = 100 batches = ~200ms total

### Optimization: Pipeline

Use Redis pipeline for faster batch deletion:

```python
async with redis.pipeline() as pipe:
    for key in keys_to_delete:
        pipe.delete(key)
    results = await pipe.execute()
    deleted_count += sum(results)
```

---

## Error Handling

### Connection Errors

```python
try:
    count = await clear_cache_pattern("/api/*")
except redis.ConnectionError as e:
    logger.error(f"Cache clear failed: {e}")
    # Don't crash the application
    return 0
```

### Partial Failures

In cluster mode, if one node fails:

```python
total_deleted = 0
errors = []

for node in nodes:
    try:
        deleted = await _clear_on_node(node, pattern)
        total_deleted += deleted
    except Exception as e:
        errors.append((node, e))

if errors:
    logger.warning(f"Cache clear partially failed: {errors}")

return total_deleted
```

---

## Testing Strategy

### Unit Tests

```python
@pytest.mark.unit
async def test_build_cache_key():
    """Test cache key construction."""
    key = _build_cache_key("/api/users", "v2", "redis:fastapi:cache")
    assert key == "redis:fastapi:cache:v2:api:users"

@pytest.mark.unit
async def test_clear_pattern_match():
    """Test glob pattern matching."""
    redis = FakeRedis()
    await redis.set("cache:api:users:1", "data1")
    await redis.set("cache:api:users:2", "data2")
    await redis.set("cache:api:items:1", "data3")
    
    count = await clear_cache_pattern("api:users:*")
    assert count == 2  # Deleted users, not items
```

### Integration Tests

```python
@pytest.mark.integration
async def test_clear_cache_pattern_integration(client):
    """Test pattern clearing end-to-end."""
    # Create cached responses
    await client.get("/users/1")  # Cache miss
    await client.get("/users/2")  # Cache miss
    await client.get("/items/1")  # Cache miss
    
    # Second requests should hit cache
    resp = await client.get("/users/1")
    assert resp.headers["X-Redis-Cache"] == "HIT"
    
    # Clear user caches
    count = await clear_cache_pattern("/users/*")
    assert count == 2
    
    # User cache cleared
    resp = await client.get("/users/1")
    assert resp.headers["X-Redis-Cache"] == "MISS"
    
    # Item cache still exists
    resp = await client.get("/items/1")
    assert resp.headers["X-Redis-Cache"] == "HIT"
```

---

## Documentation Requirements

1. **API Reference**: Document all clearing functions
2. **User Guide**: Add "Cache Invalidation" section
3. **Examples**: Show common patterns (user update, bulk operations)
4. **Warning**: Document that clearing is eventually consistent in cluster mode

---

## Acceptance Criteria

- [ ] `clear_cache()` deletes specific key
- [ ] `clear_cache_pattern()` supports glob patterns (`*`, `?`)
- [ ] `clear_cache_namespace()` clears namespace
- [ ] `clear_all_caches()` works
- [ ] Returns accurate count of deleted keys
- [ ] Uses SCAN (not KEYS)
- [ ] Works in Redis Cluster mode (scans all nodes)
- [ ] Batch deletion for efficiency
- [ ] Handles connection errors gracefully
- [ ] Full test coverage
- [ ] Documentation complete

---

## Future Enhancements (v0.3.0+)

- Async iterator for streaming deletion (large datasets)
- Dry-run mode (count matches without deleting)
- TTL refresh (extend TTL without invalidating)
- Soft delete (mark as stale, delete later)

---

## Open Questions

1. **Default namespace handling**: How to distinguish keys with no namespace vs namespace=""? → Use explicit namespace registry
2. **Progress reporting**: For large deletions, should we stream progress? → Defer to v0.3.0
3. **Rate limiting**: Should we limit deletion rate? → Not needed initially, add if users report issues
