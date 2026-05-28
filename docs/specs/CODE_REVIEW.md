# Code Review: redis-fastapi

**Date:** 2026-04-24
**Overall Rating:** 7/10 — Solid foundation, needs polish before 1.0.

---

## File-by-File Review

### 1. `__init__.py` — Good

- `__all__` is alphabetically sorted and matches imports.
- ~~`CachePending` and `CacheResponseCaptureMiddleware` are exported but appear to be implementation details — consider whether they belong in the public API.~~

### 2. `config.py` — Good, minor issues

**Positive:** Proper `pydantic-settings` with `SecretStr`, field validators, `@lru_cache` on `get_settings()`.

**Issues:**
1. `@lru_cache` prevents runtime reconfiguration. No way to clear for testing or conxfig reload.
2. ~~`db` field hard-capped at 15 (`le=15`) — Redis supports configurable DB count server-side.~~
3. ~~`ssl_check_hostname` defaults to `False` — insecure by default. Document the security implication.~~
4. ~~`default_ttl=0` semantics (no expiry) are undocumented on the field.~~
5. ~~No `model_validator` to warn when `url` is set alongside `host`/`port` (KV fields silently ignored).~~

### 3. `types.py` — Needs improvement

1. ~~`JsonCoder.encode` uses `default=str` — silently coerces non-serializable types, hiding bugs.~~
2. `KeyBuilder` type alias is `Callable[..., Union[str, Awaitable[str]]]` — too loose. Use a `Protocol` with `__call__` for proper type safety.
3. ~~`Union[str, Awaitable[str]]` used where `str | Awaitable[str]` works (file already has `from __future__ import annotations`).~~

### 4. `deps.py` — Several design concerns

1. ~~`_PoolState` is a mutable module-level singleton with no synchronization.~~
2. ~~`get_redis()` creates a new `Redis` client per call and closes it in `finally` — defeats connection pooling.~~
3. ~~`get_async_redis()` also creates a new `AsyncRedis` per call but never closes it — asymmetric with `get_redis()`.~~
4. `get_cache_backend()` creates a new `CacheBackend` per call, re-reading settings every time.
5. ~~Private `_build_*` functions are imported by `lifespan.py` — cross-module coupling of internals.~~
6. ~~Fallback pool creation when lifespan hasn't run silently works without proper lifecycle — can leak connections. Consider logging a warning.~~

### 5. `lifespan.py` — Mostly good

1. Always creates both sync AND async pools even if the app only uses one — wastes resources.
2. ~~OTel shutdown is duplicated in both cluster/non-cluster `finally` blocks — refactor with a single outer `try/finally`.~~

### 6. `cache_backend.py` — Good implementation, some concerns

1. ~~**Lua script `unpack(keys)` overflow** — Redis has a stack size limit. If `SCAN` returns thousands of keys, `unpack()` will fail. Batch the `UNLINK` calls.~~
2. ~~`_scan_delete` collects ALL keys into memory before deleting — can OOM for large namespaces.~~
3. ~~`ttl=0` and `ttl=None` both mean "no expiry" — counter-intuitive, should document clearly.~~
4. `__init__` calls `get_settings()` at construction time — couples backend to global singleton. Inject prefix directly.

### 7. `cache.py` — Complex but well-structured, significant issues

1. ~~**`CacheHitException` as control flow** — perf cost on every cache hit; clutters exception debugging.~~
2. `cache()` calls `get_settings()` at decoration time, not request time — won't pick up setting changes.
3. ~~`_resolve_redis` accesses `request.app.dependency_overrides` — internal FastAPI API, not stable.~~
4. ~~`CacheResponseCaptureMiddleware` buffers entire response body in memory — no size limit.~~
5. ~~ETag uses MD5~~ — switched to `blake2b(digest_size=16)`.
6. ~~`body_bytes.decode(errors="replace")` silently corrupts binary responses.~~
7. ~~`logger.addHandler(logging.NullHandler())` — unnecessary in Python 3.10+.~~

### 8. `rate_limit_types.py` — Clean and well-designed

1. `parse_rate` sets `max_burst=count-1` by default but `resolve_rate` uses `max_burst=0` for keyword forms — inconsistent.
2. `assert rate is not None` — use `if` instead; `assert` is stripped with `-O`.

### 9. `rate_limit.py` — Functional, design issues

1. ~~`_execute_gcra` calls `get_async_redis()` directly — bypasses DI overrides unlike `cache.py`'s `_resolve_redis`. Inconsistent, untestable.~~
2. ~~`RateLimitMiddleware` uses `BaseHTTPMiddleware` — known Starlette issues (no streaming, concurrency bugs). Use raw ASGI middleware instead.~~ — Converted to raw ASGI middleware. `RateLimitHeaderMiddleware` still uses `BaseHTTPMiddleware` (lower priority — only appends headers).
3. ~~`request.app.state.redis_settings` is accessed but never set anywhere — dead code.~~
4. ~~`blocking=True` holds the worker for the entire retry period — can exhaust workers under load.~~ — Removed `blocking` option entirely.
5. Uses `asyncio.iscoroutine` where `inspect.isawaitable` is used in `cache.py` — inconsistent.
6. `RateLimitHeaderMiddleware` and `setup.py`'s exception handler both handle `RateLimitExceeded` — dual-handling is fragile.

### 10. `setup.py` — Nice API, some concerns

1. ~~`Redis` class name shadows `redis.Redis` — confusing. Consider `RedisIntegration`.~~ — Renamed to `FastAPIRedis`; `Redis` kept as backward-compatible alias.
2. Imports `_default_exceeded_handler` — private function from `rate_limit.py`.
3. ~~No idempotency guard — calling `.caching()` or `.rate_limiting()` twice registers duplicate middleware.~~
4. ~~`self._app.router.lifespan_context` is an internal Starlette API.~~ — Accepted trade-off: stable since Starlette 0.20+, used by FastAPI's own [router lifespan merging](https://github.com/fastapi/fastapi/pull/9630), and the only way to compose lifespans without forcing the user to own the lifespan. A manual `redis_lifespan` escape hatch is documented for users who prefer explicit control. See [architecture guide](../guide/architecture.md#lifespan-wrapping).

### 11. `telemetry.py` — Well-implemented, minor issues

1. ~~7 mutable module-level globals with `PLW0603` suppression — group into a single `_OTelState` dataclass.~~
2. ~~`cache_span` is a sync context manager — span lifetime won't capture async awaits correctly. Use `start_as_current_span()`.~~ — Both `cache_span` and `rate_limit_span` now use `start_as_current_span()` for proper span nesting.
3. ~~`_ensure_rate_limit_metrics()` called on every rate limit check — should be called once in `enable_telemetry()`.~~
4. ~~`str(limited)` as OTel attribute — convention prefers boolean attributes.~~ — Now passes `limited` as a native boolean.
5. ~~No `disable_telemetry()` function — complicates testing.~~ — Added `disable_telemetry()` that resets `_state` to a fresh `_OTelState()`.

---

## Critical Issues (fix before 1.0)

1. ~~**Memory safety**: Lua `unpack()` overflow in `_DELETE_BY_PATTERN_SCRIPT`~~; ~~unbounded response buffering in `CacheResponseCaptureMiddleware`~~.
2. ~~**Resource leaks**: `get_async_redis()` creates new `AsyncRedis` per call without cleanup; `get_redis()` creates+closes per call defeating pooling.~~
3. ~~**Inconsistent Redis resolution**: `cache.py` uses `_resolve_redis` (DI-aware), `rate_limit.py` calls `get_async_redis()` directly.~~
4. ~~**Dead code**: `request.app.state.redis_settings` in `rate_limit.py` is never populated.~~

## Design Issues (consider for 1.0)

5. `CacheHitException` as control flow — performance cost, exception debugging noise.
6. ~~`BaseHTTPMiddleware` in rate limiting — use raw ASGI middleware instead.~~ — `RateLimitMiddleware` converted to raw ASGI.
7. ~~`Redis` class name shadows `redis.Redis`.~~
8. ~~No idempotency in setup — double-calling `.caching()` registers duplicate middleware.~~
9. ~~7 mutable module-level globals in `telemetry.py` — group into state object.~~

## Performance Concerns

10. Pipeline in `cache()` is good (GET+TTL in one round trip).
11. ~~New client instances per-request in `deps.py` add unnecessary overhead.~~
12. ~~`blocking=True` rate limiting holds workers — document risk.~~ — Removed `blocking` option entirely.
13. ~~MD5 for ETags~~ — switched to `hashlib.blake2b`.

## Python 3.10+ Specific

- ~~`Union[X, Y]` used where `X | Y` works (with `from __future__ import annotations` already present).~~ — Replaced with `X | Y` syntax.
- ~~`NullHandler` addition is unnecessary in 3.10+.~~ — Already removed.
- Could leverage `match` statements in `_parse_cache_control` and rate string parsing. — Not applicable; no discrete value branching to benefit from `match`.
- ~~`TypeAlias` from `typing` (3.10) should annotate `SyncClient`, `AsyncClient`, `KeyBuilder`.~~ — Added `TypeAlias` annotations to all type aliases.
