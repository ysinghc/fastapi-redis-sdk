# redis-fastapi demo app

Minimal FastAPI application that showcases every caching pattern provided by
`redis-fastapi`. Designed for one-click deployment to
[FastAPI Cloud](https://fastapicloud.com/).

## Prerequisites

- Python 3.10+
- A Redis 7.4+ server (local or [Redis Cloud](https://redis.io/cloud/))
- [uv](https://docs.astral.sh/uv/) package manager
- (optional) [FastAPI Cloud CLI](https://fastapicloud.com/docs/fastapi-cloud-cli/) for cloud deployment

## Run locally

```bash
# from the repository root
uv sync --all-groups
export REDIS_URL=redis://localhost:6379/0
uv run fastapi dev examples/main.py
```

The app is served at `http://localhost:8000`.

## Deploy to FastAPI Cloud

1. Push the repo to GitHub.
2. Link the repo in the FastAPI Cloud dashboard (or `fastapi cloud deploy`).
3. Go to your app's **Storage** tab and connect a Redis Cloud database.
   FastAPI Cloud injects `REDIS_URL` automatically — no extra config needed.
4. The `[tool.fastapi] entrypoint` in `pyproject.toml` points to
   `examples.main:app`, so the app is discovered on deploy.

Optionally set additional env vars:

```bash
fastapi cloud env set REDIS_PREFIX myapp
fastapi cloud env set REDIS_DEFAULT_TTL 300
```

## Endpoints

| Method   | Path              | Description                                      |
|----------|-------------------|--------------------------------------------------|
| `GET`    | `/`               | Health check (no Redis required)                 |
| `GET`    | `/ping`           | Redis `PING` — verify connectivity               |
| `GET`    | `/config`         | Non-sensitive connection settings                |
| `GET`    | `/cache-demo`     | DI-based cached response (30 s TTL)              |
| `DELETE` | `/cache-demo`     | Evict the `/cache-demo` cache entry              |
| `GET`    | `/items/{item_id}`| Conditional caching via `CacheBackend`           |
| `DELETE` | `/items/{item_id}`| Evict a single item from the cache               |

## Test scenarios

Replace `BASE` with your deployment URL or `http://localhost:8000`.

```bash
BASE=https://redis-fastapi-4eb7c8a2.fastapicloud.dev
```

### 1. Health check

```bash
curl $BASE/
# {"status":"ok","library":"redis-fastapi"}
```

### 2. Redis connectivity

```bash
curl $BASE/ping
# {"ping":"True"}
```

### 3. Configuration

```bash
curl $BASE/config
# {"host":"...","port":6379,"db":0,"cluster":false,"prefix":"redis:fastapi","default_ttl":0}
```

### 4. DI-based caching (`cache()` / `cache_evict()`)

```bash
# First request — MISS, response is computed and stored
curl -v $BASE/cache-demo 2>&1 | grep -E "x-redis-cache|generated_at"
# x-redis-cache: MISS
# {"generated_at":"2026-05-19T16:43:44.027887+00:00"}

# Second request within 30 s — HIT, same timestamp
curl -v $BASE/cache-demo 2>&1 | grep -E "x-redis-cache|generated_at"
# x-redis-cache: HIT
# {"generated_at":"2026-05-19T16:43:44.027887+00:00"}

# Evict the entry
curl -X DELETE $BASE/cache-demo
# {"evicted":"demo"}

# Next request — MISS again, new timestamp
curl -v $BASE/cache-demo 2>&1 | grep -E "x-redis-cache|generated_at"
# x-redis-cache: MISS
```

### 5. Conditional caching (`CacheBackend`)

Even IDs are "published" (cached for 60 s). Odd IDs are "draft" (never cached).

```bash
# Published item (even ID) — first call computes
curl $BASE/items/2
# {"id":2,"status":"published","fetched_at":"...","source":"computed"}

# Same call — served from cache (same fetched_at timestamp)
curl $BASE/items/2
# {"id":2,"status":"published","fetched_at":"...","source":"cache"}

# Draft item (odd ID) — never cached
curl $BASE/items/3
# {"id":3,"status":"draft","fetched_at":"...","source":"computed"}

# Call again — still computed (different fetched_at timestamp)
curl $BASE/items/3
# {"id":3,"status":"draft","fetched_at":"...","source":"computed"}

# Evict the published item
curl -X DELETE $BASE/items/2
# {"id":2,"deleted":true}

# Published item is recomputed after eviction
curl $BASE/items/2
# {"id":2,"status":"published","fetched_at":"...","source":"computed"}
```

### 6. HTTP cache headers

```bash
# Inspect all cache-related headers
curl -s -D - $BASE/cache-demo -o /dev/null | grep -iE "cache-control|etag|x-redis-cache"
# cache-control: max-age=30
# etag: W/"..."
# x-redis-cache: MISS

# Second request — note max-age decreases as TTL counts down
curl -s -D - $BASE/cache-demo -o /dev/null | grep -iE "cache-control|etag|x-redis-cache"
# cache-control: max-age=27
# etag: W/"..."
# x-redis-cache: HIT
```

### 7. 304 Not Modified (ETag)

```bash
# Get the ETag from a first request
ETAG=$(curl -s -D - $BASE/cache-demo -o /dev/null | grep -i etag | tr -d '\r' | awk '{print $2}')

# Send If-None-Match — server returns 304 with no body
curl -v -H "If-None-Match: $ETAG" $BASE/cache-demo 2>&1 | grep "< HTTP"
# < HTTP/2 304
```
