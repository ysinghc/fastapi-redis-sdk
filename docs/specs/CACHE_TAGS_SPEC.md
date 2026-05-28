# Cache Tags - Specification

## Overview

**Feature**: Cache Tags for Grouped Invalidation  
**Priority**: Medium (v0.3.0)  
**Effort**: Medium-High (2 weeks)  
**Status**: Draft Specification

## Problem Statement

Current invalidation options:

1. **Exact key**: `clear_cache("/users/123")` - too specific
2. **Pattern**: `clear_cache_pattern("/users/*")` - too broad, expensive (SCAN)
3. **Namespace**: `clear_cache_namespace("v2")` - too coarse

**Need**: Group related caches logically and invalidate by group.

### Use Cases

#### Use Case 1: User-related caches

```python
# Multiple endpoints cache user data
@cache(ttl=300, tags=["user:{user_id}"])
async def get_user(user_id: int): ...

@cache(ttl=300, tags=["user:{user_id}"])
async def get_user_posts(user_id: int): ...

@cache(ttl=300, tags=["user:{user_id}"])
async def get_user_profile(user_id: int): ...

# When user updates, invalidate ALL user-related caches
await invalidate_tags(f"user:{user_id}")
# → Clears get_user, get_user_posts, get_user_profile
```

#### Use Case 2: Multi-entity dependencies

```python
# Post depends on both user and category
@cache(ttl=300, tags=["user:{user_id}", "category:{cat_id}", "post:{post_id}"])
async def get_post(post_id: int, user_id: int, cat_id: int): ...

# Invalidate all posts by user
await invalidate_tags(f"user:{user_id}")

# Or invalidate all posts in category
await invalidate_tags(f"category:{cat_id}")
```

#### Use Case 3: Hierarchical invalidation

```python
# Tag hierarchy: organization → team → user
@cache(ttl=300, tags=["org:{org_id}", "team:{team_id}", "user:{user_id}"])
async def get_user_permissions(org_id: int, team_id: int, user_id: int): ...

# Invalidate entire organization
await invalidate_tags(f"org:{org_id}")
```

## Goals

1. Tag cache entries with one or more labels
2. Invalidate all caches matching tag(s)
3. Support dynamic tags (e.g., `user:{user_id}` from path params)
4. Efficient tag → key lookup (O(1) per tag, O(N) per key where N = tagged keys)
5. Automatic cleanup when cache expires
6. Support distributed invalidation (via Redis Streams)

## Non-Goals

- Tag hierarchies/inheritance (flat tags only)
- Tag metadata (tags are just strings)
- Tag-based TTL (all tagged caches use endpoint TTL)
- Transactional guarantees

---

## API Design

### Basic Usage

```python
from redis_fastapi import cache, invalidate_tags


@app.get("/users/{user_id}")
@cache(ttl=300)
async def get_user(user_id: int):
    return fetch_user(user_id)


@app.post("/users/{user_id}")
async def update_user(user_id: int, data: dict):
    save_user(user_id, data)

    # Invalidate all caches tagged with this user
    await invalidate_tags(f"user:{user_id}")

    return {"status": "updated"}
```

### Multiple Tags

```python
@cache(ttl=300, tags=["user:{user_id}", "org:{org_id}"])
async def get_user_in_org(user_id: int, org_id: int):
    return fetch_user_org_data(user_id, org_id)

# Invalidate by either tag
await invalidate_tags(f"user:{user_id}")  # OR
await invalidate_tags(f"org:{org_id}")
```

### Multiple Tags at Once

```python
# Invalidate multiple tags (union of all tagged keys)
await invalidate_tags(["user:123", "user:456"])
# → Invalidates caches tagged with user:123 OR user:456
```

### Static Tags

```python
# Tags without placeholders
@cache(ttl=300, tags=["admin-panel"])
async def get_admin_data():
    return admin_stats()

await invalidate_tags("admin-panel")
```

### Inspect Tags

```python
from redis_fastapi import get_cache_tags, get_keys_for_tag

# Get all known tags
tags = await get_cache_tags()
# → ["user:1", "user:2", "org:10", "admin-panel"]

# Get all cache keys for a tag
keys = await get_keys_for_tag("user:1")
# → ["redis:fastapi:cache:users:1", "redis:fastapi:cache:users:1:posts"]
```

---

## Implementation

### Storage Structure

#### Tag → Keys Mapping

Use Redis SETs to map tags to cache keys:

```
Key: {prefix}:tags:{tag}
Type: SET
Members: [cache_key_1, cache_key_2, ...]

Example:
  redis:fastapi:tags:user:123 → {
    "redis:fastapi:cache:users:123",
    "redis:fastapi:cache:users:123:posts",
    "redis:fastapi:cache:users:123:profile"
  }
```

#### Key → Tags Mapping (Optional)

For automatic cleanup, track which tags belong to each cache key:

```
Key: {prefix}:key_tags:{cache_key}
Type: SET
Members: [tag_1, tag_2, ...]

Example:
  redis:fastapi:key_tags:redis:fastapi:cache:users:123 → {
    "user:123",
    "org:10"
  }
```

**Trade-off**: Extra storage vs automatic cleanup

**Decision**: Implement key → tags mapping for automatic cleanup.

---

### Cache Decorator Integration

Modify `@cache` to accept `tags` parameter:

```python
def cache(
    ttl: int | None = None,
    namespace: str = "",
    prefix: str | None = None,
    coder: type[Coder] | None = None,
    key_builder: KeyBuilder | None = None,
    tags: list[str] | None = None,  # NEW
) -> Callable:
    """Cache decorator with tag support.
    
    Args:
        tags: List of tags for this cache. Supports placeholders like
              "user:{user_id}" which are resolved from function params.
    """
    ...
```

### Tag Resolution

Resolve tag placeholders from function arguments:

```python
def _resolve_tags(tags: list[str], func_kwargs: dict[str, Any]) -> list[str]:
    """Resolve tag placeholders from function kwargs.
    
    Args:
        tags: Tag templates like ["user:{user_id}", "org:{org_id}"]
        func_kwargs: Function arguments like {"user_id": 123, "org_id": 10}
    
    Returns:
        Resolved tags like ["user:123", "org:10"]
    """
    resolved = []
    for tag in tags:
        # Simple string formatting
        try:
            resolved_tag = tag.format(**func_kwargs)
            resolved.append(resolved_tag)
        except KeyError as e:
            logger.warning(f"Tag placeholder not found: {e} in tag '{tag}'")
            # Skip unresolved tags
    return resolved
```

### Storing Tags

When caching a value, also store tag mappings:

```python
async def _cache_with_tags(
    redis: AsyncRedis,
    cache_key: str,
    value: str,
    ttl: int,
    tags: list[str],
):
    """Store cache value and tag mappings."""
    # Store cache value
    await redis.setex(cache_key, ttl, value)
    
    if not tags:
        return
    
    # Store tag → key mappings
    for tag in tags:
        tag_key = f"{settings.pattern_prefix('tags')}:{tag}"
        await redis.sadd(tag_key, cache_key)
        # Set expiry on tag set (same as cache TTL)
        await redis.expire(tag_key, ttl)
    
    # Store key → tags mapping (for cleanup)
    key_tags_key = f"{settings.pattern_prefix('key_tags')}:{cache_key}"
    await redis.sadd(key_tags_key, *tags)
    await redis.expire(key_tags_key, ttl)
```

### Invalidating by Tag

```python
async def invalidate_tags(
    tags: str | list[str],
    *,
    prefix: str | None = None,
    distributed: bool = False,
) -> int:
    """Invalidate all cache keys tagged with given tag(s).
    
    Args:
        tags: Tag or list of tags to invalidate
        prefix: Cache prefix override
        distributed: If True, publish to distributed invalidation stream
    
    Returns:
        Number of keys deleted
    """
    redis = await get_async_redis()
    _prefix = prefix or settings.pattern_prefix("tags")
    
    tag_list = [tags] if isinstance(tags, str) else tags
    
    # Collect all cache keys for these tags
    keys_to_delete = set()
    for tag in tag_list:
        tag_key = f"{_prefix}:{tag}"
        cache_keys = await redis.smembers(tag_key)
        keys_to_delete.update(cache_keys)
    
    if not keys_to_delete:
        return 0
    
    # Delete cache keys
    deleted = await redis.delete(*keys_to_delete)
    
    # Clean up tag sets
    for tag in tag_list:
        tag_key = f"{_prefix}:{tag}"
        await redis.delete(tag_key)
    
    # Clean up key_tags sets
    key_tags_keys = [
        f"{settings.pattern_prefix('key_tags')}:{key}"
        for key in keys_to_delete
    ]
    await redis.delete(*key_tags_keys)
    
    # Optional: Publish distributed invalidation
    if distributed and settings.enable_distributed_invalidation:
        await _publish_tag_invalidation(tag_list)
    
    return deleted
```

---

## Automatic Cleanup

### Problem

When cache expires, tag mappings become stale:

```
Cache key expires (TTL) → Key deleted
Tag SET still references key → Stale reference
```

### Solution 1: TTL on Tag SETs

Set same TTL on tag SETs as cache:

```python
await redis.sadd(tag_key, cache_key)
await redis.expire(tag_key, ttl)  # Same TTL as cache
```

**Issue**: If multiple keys share tag with different TTLs, tag SET expires prematurely.

### Solution 2: Lazy Cleanup

When invalidating by tag, filter out non-existent keys:

```python
cache_keys = await redis.smembers(tag_key)

# Filter out expired keys
existing_keys = []
for key in cache_keys:
    if await redis.exists(key):
        existing_keys.append(key)

deleted = await redis.delete(*existing_keys)
```

**Issue**: Adds latency (O(N) EXISTS checks).

### Solution 3: Background Cleanup

Periodic task cleans up stale tag references:

```python
async def cleanup_stale_tags():
    """Remove stale key references from tag sets."""
    pattern = f"{settings.pattern_prefix('tags')}:*"
    
    async for tag_key in redis.scan_iter(match=pattern):
        cache_keys = await redis.smembers(tag_key)
        stale_keys = []
        
        for key in cache_keys:
            if not await redis.exists(key):
                stale_keys.append(key)
        
        if stale_keys:
            await redis.srem(tag_key, *stale_keys)
        
        # If tag set is empty, delete it
        if await redis.scard(tag_key) == 0:
            await redis.delete(tag_key)
```

**Decision**: Use Solution 1 (TTL on tag SETs) + Solution 2 (lazy cleanup during invalidation).

---

## Configuration

```python
@dataclass
class RedisSettings:
    # ... existing ...
    
    # Cache tags
    enable_cache_tags: bool = True
    cache_tags_cleanup_interval: int = 3600  # Background cleanup (seconds)
```

Environment variables:

```bash
export REDIS_ENABLE_CACHE_TAGS=true
export REDIS_CACHE_TAGS_CLEANUP_INTERVAL=3600
```

---

## Performance Considerations

### Storage Overhead

Per tagged cache entry:
- Cache key: ~100 bytes
- Tag SET: ~50 bytes per tag
- Key_tags SET: ~50 bytes per tag

Example: Cache with 3 tags
- Cache: 100 bytes
- 3 tag SETs: 150 bytes
- Key_tags: 150 bytes
- **Total**: 400 bytes (~4x overhead)

**Mitigation**: Tags are optional, only used when needed.

### Invalidation Performance

Invalidating by tag:
- `SMEMBERS` tag SET: O(N) where N = tagged keys
- `DELETE` keys: O(K) where K = number of keys
- **Total**: O(N + K)

For 100 tagged keys: ~10-20ms

**Comparison**: Pattern-based clearing (`SCAN`) can take seconds for large datasets.

### Tag SET Expiry

Setting TTL on tag SETs:
- `EXPIRE`: O(1)
- Minimal overhead

---

## Distributed Invalidation Integration

### Publishing Tag Invalidation Events

```python
async def _publish_tag_invalidation(tags: list[str]):
    """Publish tag invalidation to distributed stream."""
    event = {
        "type": "tag_invalidation",
        "tags": ",".join(tags),
        "timestamp": str(time.time()),
        "instance_id": INSTANCE_ID,
    }
    await redis.xadd(settings.invalidation_stream_name, event)
```

### Consuming Tag Invalidation Events

```python
async def _process_tag_invalidation_event(event: dict):
    """Process tag invalidation event from stream."""
    tags = event[b"tags"].decode().split(",")
    instance_id = event[b"instance_id"].decode()
    
    if instance_id == INSTANCE_ID:
        return  # Skip own events
    
    # Invalidate locally
    await invalidate_tags(tags, distributed=False)
```

---

## Testing Strategy

### Unit Tests

```python
@pytest.mark.unit
async def test_resolve_tags():
    """Test tag placeholder resolution."""
    tags = ["user:{user_id}", "org:{org_id}"]
    kwargs = {"user_id": 123, "org_id": 10}
    
    resolved = _resolve_tags(tags, kwargs)
    assert resolved == ["user:123", "org:10"]

@pytest.mark.unit
async def test_invalidate_by_tag():
    """Test tag invalidation."""
    redis = FakeRedis()
    
    # Store cache with tags
    await _cache_with_tags(
        redis, "cache:key1", "value1", 300, ["user:1", "org:10"]
    )
    
    # Invalidate by tag
    count = await invalidate_tags("user:1")
    assert count == 1
    
    # Cache should be gone
    assert await redis.get("cache:key1") is None
```

### Integration Tests

```python
@pytest.mark.integration
async def test_cache_tags_e2e(client):
    """Test cache tags end-to-end."""
    
    @app.get("/users/{user_id}")
    @cache(ttl=300, tags=["user:{user_id}"])
    async def get_user(user_id: int):
        return {"id": user_id}
    
    # Cache user
    resp = await client.get("/users/1")
    assert resp.headers["X-Redis-Cache"] == "MISS"
    
    # Hit cache
    resp = await client.get("/users/1")
    assert resp.headers["X-Redis-Cache"] == "HIT"
    
    # Invalidate by tag
    await invalidate_tags("user:1")
    
    # Cache cleared
    resp = await client.get("/users/1")
    assert resp.headers["X-Redis-Cache"] == "MISS"
```

---

## Documentation

1. **User Guide**: "Cache Tags" section with use cases
2. **API Reference**: Document `tags` parameter, `invalidate_tags()`
3. **Examples**: User-entity, multi-entity, hierarchical patterns
4. **Best Practices**: When to use tags vs patterns

---

## Acceptance Criteria

- [ ] `@cache(tags=[...])` parameter works
- [ ] Tag placeholders resolved from function params
- [ ] `invalidate_tags()` clears all tagged caches
- [ ] Multiple tags per cache supported
- [ ] Tag → key mapping stored in Redis SETs
- [ ] Automatic cleanup on cache expiry
- [ ] Works with distributed invalidation
- [ ] Performance acceptable (< 20ms for 100 keys)
- [ ] Full test coverage
- [ ] Documentation complete

---

## Future Enhancements

- Tag glob patterns (`user:*`)
- Tag metadata (creation time, hit count)
- Tag-specific TTLs
- Tag hierarchies (`org:10:team:5:user:123`)

---

## Open Questions

1. **Tag naming conventions**: Enforce format? → No, allow freeform
2. **Max tags per cache**: Limit to prevent abuse? → No limit initially, add if needed
3. **Tag analytics**: Track most-used tags? → Defer to v0.4.0
