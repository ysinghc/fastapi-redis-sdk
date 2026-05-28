# Distributed Cache Invalidation via Redis Streams - Specification

## Overview

**Feature**: Distributed Cache Invalidation via Redis Streams  
**Priority**: Medium-High (v0.3.0)  
**Effort**: High (2-3 weeks)  
**Status**: Draft Specification

## Problem Statement

In multi-instance deployments, cache invalidation is local-only:

```
Instance 1: User updates → Invalidates local cache ✓
Instance 2: Still has stale cache ✗
Instance 3: Still has stale cache ✗
```

**Current workarounds**:
1. Short TTLs (wastes cache potential)
2. Manual cache clearing on each instance (not automated)
3. External cache invalidation service (complex)

**Need**: Automatic cache invalidation across all application instances.

## Goals

1. Coordinate cache invalidation across multiple app instances
2. Use Redis Streams for reliable pub/sub
3. Support pattern-based invalidation (e.g., `/users/*`)
4. Be eventually consistent (no strong guarantees needed)
5. Handle instance failures gracefully
6. Minimal latency overhead (< 10ms)

## Non-Goals

- Strong consistency (eventual consistency is acceptable)
- Ordered invalidation (order doesn't matter)
- Persistent invalidation history (streams can be trimmed)
- Cross-region invalidation (assumes single Redis instance/cluster)

---

## Architecture

### Components

1. **Invalidation Publisher**: Publishes invalidation events to Redis Stream
2. **Invalidation Listener**: Background task listening to stream on each instance
3. **Local Cache Tracker**: Tracks what's cached locally (optional optimization)
4. **Redis Stream**: Central coordination mechanism

### Flow Diagram

```
[Instance 1]                    [Redis Stream]                [Instance 2]
    |                                 |                              |
    | POST /users/1                   |                              |
    |─────────────►                   |                              |
    | Update DB                        |                              |
    | Invalidate local cache          |                              |
    | Publish event ──────────────────►|                              |
    |                                  | XADD invalidations          |
    |                                  |                              |
    |                                  | XREAD ◄──────────────────────|
    |                                  |                         Listen for events
    |                                  | Event: {"pattern": "/users/1"}|
    |                                  |──────────────────────────────►|
    |                                  |                         Clear local cache
    |                                  |                              |
```

### Redis Streams Advantages

vs Pub/Sub:
- ✅ **Persistence**: Messages not lost if consumer offline
- ✅ **Consumer groups**: Multiple instances, no duplicate processing
- ✅ **ACK mechanism**: Ensure messages processed
- ✅ **History**: Can replay missed events

vs Keyspace notifications:
- ✅ **Payload**: Can send metadata (patterns, namespaces)
- ✅ **Reliable**: Not fire-and-forget
- ✅ **Efficient**: Single stream vs many key events

---

## API Design

### Publishing Invalidation Events

```python
from redis_fastapi import invalidate_cache_distributed

@app.post("/users/{user_id}")
async def update_user(user_id: int, data: UserUpdate):
    # Update database
    await db.update_user(user_id, data)
    
    # Invalidate cache across all instances
    await invalidate_cache_distributed(f"/users/{user_id}")
    
    return {"status": "updated"}
```

### Pattern-Based Invalidation

```python
# Invalidate all user endpoints
await invalidate_cache_distributed("/users/*")

# Invalidate with namespace
await invalidate_cache_distributed("/api/*", namespace="v2")

# Invalidate specific key
await invalidate_cache_distributed("/items/123", exact=True)
```

### Multiple Patterns

```python
# Invalidate multiple patterns at once
await invalidate_cache_distributed([
    "/users/{user_id}",
    "/users/{user_id}/posts",
    "/users/{user_id}/profile",
])
```

### Configuration

```python
from redis_fastapi import RedisSettings

settings = RedisSettings(
    # ... existing fields ...
    
    # Distributed invalidation
    enable_distributed_invalidation=True,
    invalidation_stream_name="cache:invalidations",
    invalidation_consumer_group="cache-workers",
    invalidation_batch_size=10,  # Process N events per poll
    invalidation_poll_interval=0.1,  # Poll every 100ms
)
```

Environment variables:

```bash
export REDIS_ENABLE_DISTRIBUTED_INVALIDATION=true
export REDIS_INVALIDATION_STREAM_NAME=cache:invalidations
export REDIS_INVALIDATION_CONSUMER_GROUP=cache-workers
export REDIS_INVALIDATION_BATCH_SIZE=10
export REDIS_INVALIDATION_POLL_INTERVAL=0.1
```

---

## Implementation

### Stream Structure

**Stream name**: `{prefix}:invalidations`

**Message format**:

```json
{
  "pattern": "/users/*",
  "namespace": "",
  "exact": false,
  "timestamp": 1704067200.0,
  "instance_id": "instance-1-uuid"
}
```

### Publisher Implementation

```python
import time
import uuid
from redis_fastapi import get_async_redis, settings

# Generate unique instance ID on startup
INSTANCE_ID = str(uuid.uuid4())

async def invalidate_cache_distributed(
    pattern: str | list[str],
    *,
    namespace: str = "",
    exact: bool = False,
) -> int:
    """Publish cache invalidation event to all instances.
    
    Args:
        pattern: Path pattern(s) to invalidate (e.g., "/users/*")
        namespace: Namespace to invalidate
        exact: If True, invalidate exact key only (no pattern matching)
    
    Returns:
        Number of events published
    """
    if not settings.enable_distributed_invalidation:
        # Fallback to local invalidation
        return await _invalidate_local(pattern, namespace=namespace, exact=exact)
    
    redis = await get_async_redis()
    stream_name = settings.invalidation_stream_name
    
    patterns = [pattern] if isinstance(pattern, str) else pattern
    
    for pat in patterns:
        event = {
            "pattern": pat,
            "namespace": namespace,
            "exact": str(exact),  # Redis Streams: all values must be strings
            "timestamp": str(time.time()),
            "instance_id": INSTANCE_ID,
        }
        
        await redis.xadd(stream_name, event)
    
    return len(patterns)
```

### Consumer Implementation

Background task that runs on each instance:

```python
import asyncio
import logging
from redis_fastapi import get_async_redis, settings, clear_cache_pattern, clear_cache

logger = logging.getLogger(__name__)

class InvalidationConsumer:
    """Background consumer for distributed cache invalidation events."""
    
    def __init__(self):
        self.stream_name = settings.invalidation_stream_name
        self.group_name = settings.invalidation_consumer_group
        self.consumer_name = f"{INSTANCE_ID[:8]}"  # Shortened instance ID
        self.running = False
    
    async def start(self):
        """Start consuming invalidation events."""
        redis = await get_async_redis()
        
        # Create consumer group if it doesn't exist
        try:
            await redis.xgroup_create(
                self.stream_name,
                self.group_name,
                id="0",  # Start from beginning
                mkstream=True,  # Create stream if doesn't exist
            )
        except Exception as e:
            # Group might already exist
            logger.debug(f"Consumer group creation: {e}")
        
        self.running = True
        logger.info(f"Invalidation consumer started: {self.consumer_name}")
        
        while self.running:
            try:
                await self._poll_events(redis)
            except Exception as e:
                logger.error(f"Error consuming invalidation events: {e}")
                await asyncio.sleep(1)  # Back off on error
    
    async def stop(self):
        """Stop consuming events."""
        self.running = False
        logger.info(f"Invalidation consumer stopped: {self.consumer_name}")
    
    async def _poll_events(self, redis):
        """Poll for new invalidation events."""
        # Read from stream (blocking with timeout)
        events = await redis.xreadgroup(
            self.group_name,
            self.consumer_name,
            {self.stream_name: ">"},  # ">" = only new messages
            count=settings.invalidation_batch_size,
            block=int(settings.invalidation_poll_interval * 1000),  # ms
        )
        
        if not events:
            return
        
        # Process events
        for stream_name, messages in events:
            for message_id, event_data in messages:
                await self._process_event(redis, message_id, event_data)
    
    async def _process_event(self, redis, message_id: str, event: dict):
        """Process single invalidation event."""
        try:
            pattern = event[b"pattern"].decode()
            namespace = event[b"namespace"].decode()
            exact = event[b"exact"].decode() == "true"
            instance_id = event[b"instance_id"].decode()
            
            # Skip events from this instance (already invalidated locally)
            if instance_id == INSTANCE_ID:
                logger.debug(f"Skipping own event: {pattern}")
                await redis.xack(self.stream_name, self.group_name, message_id)
                return
            
            # Invalidate local cache
            if exact:
                count = await clear_cache(pattern, namespace=namespace)
            else:
                count = await clear_cache_pattern(pattern, namespace=namespace)
            
            logger.info(f"Invalidated {count} keys for pattern: {pattern}")
            
            # ACK message
            await redis.xack(self.stream_name, self.group_name, message_id)
            
        except Exception as e:
            logger.error(f"Error processing event {message_id}: {e}")
            # Don't ACK - message will be redelivered


# Global consumer instance
_consumer: InvalidationConsumer | None = None

async def start_invalidation_consumer():
    """Start the invalidation consumer background task."""
    global _consumer
    
    if not settings.enable_distributed_invalidation:
        return
    
    _consumer = InvalidationConsumer()
    asyncio.create_task(_consumer.start())

async def stop_invalidation_consumer():
    """Stop the invalidation consumer."""
    global _consumer
    
    if _consumer:
        await _consumer.stop()
```

### Lifespan Integration

Update `redis_lifespan` to start/stop consumer:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def redis_lifespan(app: FastAPI):
    """Lifespan manager with distributed invalidation support."""
    # Create connection pools
    pool_state.sync_pool = _build_sync_pool()
    pool_state.async_pool = _build_async_pool()
    
    # Start invalidation consumer
    await start_invalidation_consumer()
    
    yield
    
    # Stop invalidation consumer
    await stop_invalidation_consumer()
    
    # Close pools
    await pool_state.async_pool.aclose()
    pool_state.sync_pool.close()
```

---

## Stream Maintenance

### Trimming Old Events

Prevent unbounded stream growth:

```python
async def trim_invalidation_stream():
    """Trim old invalidation events (keep last 10,000)."""
    redis = await get_async_redis()
    await redis.xtrim(
        settings.invalidation_stream_name,
        maxlen=10000,
        approximate=True,  # Faster, allows some slack
    )
```

Call periodically (e.g., every hour):

```python
# In lifespan startup
async def periodic_trim():
    while True:
        await asyncio.sleep(3600)  # 1 hour
        await trim_invalidation_stream()

asyncio.create_task(periodic_trim())
```

### Configuration

```python
@dataclass
class RedisSettings:
    # ... existing ...
    
    invalidation_stream_maxlen: int = 10000
    invalidation_stream_trim_interval: int = 3600  # seconds
```

---

## Error Handling

### Consumer Crashes

Consumer groups ensure reliability:
- Unprocessed messages remain in stream
- Other consumers can pick up pending messages
- Use `XPENDING` to check for stuck messages

### Redis Connection Loss

```python
async def _poll_events(self, redis):
    try:
        events = await redis.xreadgroup(...)
    except redis.ConnectionError:
        logger.warning("Lost Redis connection, retrying...")
        await asyncio.sleep(5)
        return
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await asyncio.sleep(1)
```

### Publisher Failures

If publishing fails, fall back to local invalidation:

```python
async def invalidate_cache_distributed(pattern, **kwargs):
    try:
        # Try distributed
        await redis.xadd(stream_name, event)
    except Exception as e:
        logger.warning(f"Distributed invalidation failed: {e}, falling back to local")
        await clear_cache_pattern(pattern, **kwargs)
```

---

## Performance Considerations

### Latency

- **Publish**: `XADD` ~0.5ms
- **Poll interval**: 100ms (configurable)
- **Processing**: < 5ms per event
- **Total**: ~105ms worst-case latency for invalidation propagation

### Throughput

- Redis Streams: ~100,000 messages/sec
- Typical invalidation rate: < 100/sec
- **Conclusion**: Not a bottleneck

### Memory

- Stream size: ~1KB per message
- 10,000 messages = ~10MB
- Trimming keeps memory bounded

---

## Testing Strategy

### Unit Tests

```python
@pytest.mark.unit
async def test_publish_invalidation_event():
    """Test event publishing."""
    redis = FakeRedis()
    await invalidate_cache_distributed("/users/*")
    
    # Check event in stream
    events = await redis.xread({stream_name: "0"})
    assert len(events[0][1]) == 1
    assert events[0][1][0][1][b"pattern"] == b"/users/*"
```

### Integration Tests

```python
@pytest.mark.integration
async def test_distributed_invalidation_multi_instance(client1, client2):
    """Test invalidation across instances."""
    # Instance 1: Cache response
    resp = await client1.get("/users/1")
    assert resp.headers["X-Redis-Cache"] == "MISS"
    
    # Instance 2: Cache same response
    resp = await client2.get("/users/1")
    assert resp.headers["X-Redis-Cache"] == "HIT"
    
    # Instance 1: Update user → publish invalidation
    await client1.post("/users/1", json={"name": "Updated"})
    
    # Wait for propagation
    await asyncio.sleep(0.2)
    
    # Instance 2: Cache should be invalidated
    resp = await client2.get("/users/1")
    assert resp.headers["X-Redis-Cache"] == "MISS"
```

---

## Documentation

1. **User Guide**: "Distributed Cache Invalidation" section
2. **Configuration**: All env vars documented
3. **Architecture**: Diagram showing multi-instance setup
4. **Best Practices**: When to use distributed vs local invalidation

---

## Acceptance Criteria

- [ ] Invalidation events published to Redis Stream
- [ ] Consumer reads and processes events
- [ ] Cross-instance invalidation works
- [ ] Consumer groups for reliability
- [ ] Stream trimming prevents unbounded growth
- [ ] Handles Redis connection failures
- [ ] Minimal latency (< 10ms overhead)
- [ ] Works in Redis Cluster mode
- [ ] Can be disabled via config
- [ ] Full test coverage
- [ ] Documentation complete

---

## Future Enhancements

- Invalidation metrics (events published/processed)
- Dead letter queue for failed invalidations
- TTL-based invalidation (refresh TTL without clearing)
- Regional invalidation (multi-region Redis)

---

## Open Questions

1. **Consumer naming**: Use instance hostname or UUID? → UUID (portable)
2. **Stream retention**: How long to keep events? → Trim to 10K messages
3. **Cluster mode**: One stream or per-node streams? → Single stream with hash tag
