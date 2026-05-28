# Performance Optimizations for redis-fastapi Caching

Based on redis-py best practices and production deployment patterns, this document outlines potential performance improvements for the caching system.

---

## Current State Analysis

### What We Already Do Well ✅

1. **Connection Pooling**: Using shared connection pools via `pool_state`
2. **Async I/O**: Fully async Redis operations (no blocking)
3. **Graceful Degradation**: Errors don't crash the app
4. **Efficient Serialization**: JSON encoding (fast for most cases)

### Current Cache Flow Performance

**Cache HIT** (2 Redis commands):
```python
cached = await redis.get(cache_key)        # Command 1: GET
remaining_ttl = await redis.ttl(cache_key) # Command 2: TTL
```
**Total**: ~2ms (2 network round trips)

**Cache MISS** (1 Redis command):
```python
await redis.set(cache_key, encoded, ex=ttl)  # Command 1: SETEX
```
**Total**: Handler time + ~1ms

---

## Optimization Opportunities

### 1. **Pipelining for Cache HIT** 🚀 HIGH IMPACT

**Problem**: Cache HIT currently makes 2 separate Redis calls (`GET` + `TTL`)

**Current**:
```python
# 2 network round trips
raw = await redis.get(cache_key)         # ~1ms
t = await redis.ttl(cache_key)           # ~1ms
# Total: ~2ms
```

**Optimized** (use pipeline):
```python
# 1 network round trip
async with redis.pipeline(transaction=False) as pipe:
    pipe.get(cache_key)
    pipe.ttl(cache_key)
    raw, t = await pipe.execute()
# Total: ~1ms
```

**Impact**: 
- ✅ **50% reduction** in cache HIT latency (2ms → 1ms)
- ✅ Reduced network traffic
- ✅ Lower Redis server load

**Complexity**: **Low** (5 lines changed)

**Implementation**:
```python
# In cache.py, replace lines 196-200
if not force_refresh:
    async with redis.pipeline(transaction=False) as pipe:
        pipe.get(cache_key)
        pipe.ttl(cache_key)
        raw, t = await pipe.execute()
        if raw is not None:
            cached = raw.decode() if isinstance(raw, bytes) else raw
            remaining_ttl = max(t, 0)
```

---

### 2. **Client-Side Caching** 🔥 VERY HIGH IMPACT

**What is it?**: redis-py 5.1.0+ supports client-side caching (tracking mode 2)

**How it works**:
- Redis tracks which keys the client reads
- Redis notifies client when keys change
- Client caches values locally (no network call)

**Benefits**:
- ✅ **~100x faster** cache hits (μs instead of ms)
- ✅ Zero network traffic for cached reads
- ✅ Automatic invalidation via Redis notifications

**Trade-offs**:
- ⚠️ Requires Redis 6.0+ (RESP3 protocol)
- ⚠️ Extra memory on client side
- ⚠️ Complexity in multi-instance deployments

**Configuration**:
```python
from redis.asyncio import Redis

redis = Redis(
    ...,
    protocol=3,  # RESP3 required for client-side caching
    cache_config={
        "max_size": 1000,  # Max cached items
        "ttl": 60,         # Local cache TTL
        "policy": "lru",   # Eviction policy
    }
)
```

**Impact**:
- ✅ Cache HIT: **2ms → 0.02ms** (100x improvement)
- ✅ Offloads Redis server
- ⚠️ Adds ~1-10MB RAM per instance

**Recommendation**: **Add as opt-in feature in v0.2.0**

---

### 3. **Connection Pool Tuning** 🟡 MEDIUM IMPACT

**Current**: Default pool settings (unbounded)

**Best practices** from redis-py and AWS ElastiCache docs:

#### a) Set `max_connections` based on concurrency

```python
# Calculate optimal pool size
max_connections = concurrent_requests × redis_ops_per_request × 1.5

# Example: 100 concurrent requests, 2 Redis ops each
# max_connections = 100 × 2 × 1.5 = 300
```

**Current default**: Unbounded (can exhaust Redis server connections)

**Recommendation**:
```python
# In config.py
max_connections: int = 50  # Change from None to reasonable default
```

#### b) Use `BlockingConnectionPool` for backpressure

**Problem**: Default pool raises exception when full

**Solution**: Block and wait for available connection

```python
from redis.asyncio import BlockingConnectionPool

pool = BlockingConnectionPool(
    ...,
    max_connections=50,
    timeout=5,  # Wait up to 5s for connection
)
```

**Impact**:
- ✅ Prevents "No connections available" errors
- ✅ Automatic backpressure
- ✅ More predictable behavior under load

**Complexity**: **Low**

---

### 4. **Health Check Interval** 🟡 MEDIUM IMPACT

**Problem**: Stale connections in pool can cause errors

**Solution**: Enable periodic health checks

```python
# In config.py
health_check_interval: int = 30  # Seconds between checks
```

**Usage**:
```python
pool = ConnectionPool(
    ...,
    health_check_interval=settings.health_check_interval,
)
```

**Impact**:
- ✅ Detects dead connections proactively
- ✅ Reduces error rate in production
- ⚠️ Slight overhead (~1 PING per connection per interval)

**Recommendation**: **Enable by default with 30s interval**

---

### 5. **Socket Keepalive** 🟢 LOW IMPACT

**Problem**: Idle connections closed by firewalls/load balancers

**Solution**: Enable TCP keepalive

```python
# In config.py
socket_keepalive: bool = True
socket_keepalive_options: dict = {
    socket.TCP_KEEPIDLE: 60,
    socket.TCP_KEEPINTVL: 10,
    socket.TCP_KEEPCNT: 3,
}
```

**Impact**:
- ✅ Prevents idle connection drops
- ✅ Better for long-lived applications

---

### 6. **Retry Logic** 🟡 MEDIUM IMPACT

**Problem**: Transient Redis errors cause cache miss

**Solution**: Automatic retries for read operations

```python
from redis.retry import Retry
from redis.backoff import ExponentialBackoff

retry = Retry(
    ExponentialBackoff(cap=10, base=1),
    retries=3,
)

pool = ConnectionPool(
    ...,
    retry=retry,
    retry_on_error=[ConnectionError, TimeoutError],
)
```

**Impact**:
- ✅ Higher cache hit rate during transient failures
- ✅ Better resilience
- ⚠️ Can add latency on failures

**Recommendation**: **Enable for read-only operations**

---

### 7. **Compression** 🟢 LOW-MEDIUM IMPACT

**Problem**: Large responses waste bandwidth and memory

**Solution**: Compress cached values

```python
import zlib

class CompressedCoder:
    @classmethod
    def encode(cls, value) -> str:
        json_str = json.dumps(value)
        compressed = zlib.compress(json_str.encode(), level=6)
        return base64.b64encode(compressed).decode()
    
    @classmethod
    def decode(cls, value: str):
        compressed = base64.b64decode(value.encode())
        json_str = zlib.decompress(compressed).decode()
        return json.loads(json_str)

@cache(ttl=60, coder=CompressedCoder)
async def large_response():
    return {"data": [...]}  # Large payload
```

**Impact**:
- ✅ **50-80% size reduction** for JSON
- ✅ Lower memory usage
- ✅ Faster network transfer
- ⚠️ CPU overhead (compress/decompress)

**Recommendation**: **Provide as optional coder** (v0.2.0)

---

### 8. **Decode Responses Setting** 🟢 LOW IMPACT

**Current**: Manual decode: `raw.decode() if isinstance(raw, bytes) else raw`

**Optimization**: Use `decode_responses=True`

```python
redis = Redis(
    ...,
    decode_responses=True,  # Auto-decode bytes to str
)
```

**Impact**:
- ✅ Cleaner code (no manual decoding)
- ⚠️ Small performance cost (auto-decode everything)

**Note**: May conflict with binary values. Use selectively.

---

### 9. **Single Command Optimization** 🟢 LOW IMPACT

**GETEX** (Redis 6.2+) combines GET + EXPIRE:

```python
# Current (2 commands)
value = await redis.get(cache_key)
ttl = await redis.ttl(cache_key)

# Optimized (1 command) - if we wanted to refresh TTL on read
value = await redis.getex(cache_key, ex=ttl)
```

**Note**: Not directly applicable (we don't want sliding TTL), but useful for other patterns.

---

## Recommended Implementation Priority

### **Phase 1: Quick Wins (v0.2.0)** - 1 day

1. ✅ **Pipelining for cache HIT** (50% latency reduction)
2. ✅ **Set default `max_connections=50`**
3. ✅ **Enable `health_check_interval=30`**
4. ✅ **Use `BlockingConnectionPool`**

**Expected improvement**: **40-60% faster cache HITs**

### **Phase 2: Advanced (v0.3.0)** - 1 week

5. ✅ **Client-side caching** (opt-in via config)
6. ✅ **Retry logic** for resilience
7. ✅ **Compression coder** (opt-in)

**Expected improvement**: **Up to 100x for client-side caching**

---

## Configuration Changes Needed

```python
# In config.py
@dataclass
class RedisSettings:
    # ... existing fields ...
    
    # Connection pool (NEW)
    max_connections: int = 50  # Default instead of None
    health_check_interval: int = 30  # Enable health checks
    socket_keepalive: bool = True
    
    # Client-side caching (NEW, opt-in)
    enable_client_cache: bool = False
    client_cache_max_size: int = 1000
    client_cache_ttl: int = 60
    
    # Retry logic (NEW)
    enable_retry: bool = True
    retry_max_attempts: int = 3
    retry_backoff_base: float = 1.0
    retry_backoff_cap: float = 10.0
```

---

## Performance Comparison

| Scenario | Current | With Pipelining | With Client-Side Cache |
|----------|---------|----------------|----------------------|
| Cache HIT | ~2ms | ~1ms | ~0.02ms |
| Cache MISS | Handler + 1ms | Handler + 1ms | Handler + 1ms |
| Network calls (HIT) | 2 | 1 | 0 |
| Throughput (HIT) | 500 req/s | 1000 req/s | 50,000 req/s |

**Assumptions**: 1ms network latency, handler takes 10ms

---

## Summary

**Easiest, highest impact** (implement first):
1. **Pipelining** - 5 lines of code, 50% latency reduction
2. **Connection pool defaults** - 2 lines, better reliability

**Advanced, massive impact** (implement later):
3. **Client-side caching** - Medium complexity, 100x improvement

**Nice to have**:
4. Health checks, retry logic, compression

---

**Next Steps**: Would you like me to implement Phase 1 optimizations (pipelining + pool tuning)?
