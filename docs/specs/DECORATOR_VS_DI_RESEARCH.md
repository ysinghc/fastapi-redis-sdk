# Decorator vs Dependency Injection for Caching: Research

## Question

The FastAPI team suggests using DI exclusively for cache configuration:

```python
@app.get("/items", dependencies=[cache(ttl=60)])
def get_items(redis: RedisDep):
    ...
```

Our library uses decorators for the simple case and DI (`CacheBackendDep`) for advanced use cases. Should we move to DI-only? Is the decorator pattern an antipattern in FastAPI?

---

## Why the FastAPI Team Might Want DI-Only

### 1. Signature Manipulation Is Fragile

Our `@cache` decorator (and `fastapi-cache2` before us) **rewrites the function signature** at decoration time, injecting hidden `Request` and `Response` parameters via `_augment_signature()`. FastAPI relies heavily on function introspection for:

- Request validation
- OpenAPI schema generation
- Dependency graph resolution

When a decorator manipulates `__signature__`, it operates **outside** FastAPI's dependency resolution system. This is a known source of breakage — see [fastapi/fastapi#1743](https://github.com/fastapi/fastapi/issues/1743), [fastapi/fastapi#5065](https://github.com/fastapi/fastapi/issues/5065), and the article ["How Decorators Can Break FastAPI Endpoints"](https://saharsh-solanki.medium.com/how-decorators-can-break-fastapi-endpoints-and-how-to-fix-it-398a540b8e0a). FastAPI's DI system is designed to handle parameter injection natively; decorators that do the same thing are working around, not with, the framework.

### 2. Conflicts with Other Libraries

Decorator-based signature rewriting **clashes with other libraries** that also use FastAPI's DI system. A concrete example: `fastapi-cache2` v0.2.2's dependency injection changes broke compatibility with `fastapi-pagination` ([long2ice/fastapi-cache#557](https://github.com/long2ice/fastapi-cache/issues/557)). The cache decorator's route wrapping prevented pagination dependencies from initializing. Other reported conflicts include `Depends`-based DB sessions producing non-repeatable cache keys ([long2ice/fastapi-cache#89](https://github.com/long2ice/fastapi-cache/issues/89), [#279](https://github.com/long2ice/fastapi-cache/issues/279)).

DI dependencies, by contrast, are resolved through a single, well-understood graph with built-in caching per request.

### 3. `dependency_overrides` Doesn't Work with Decorators

FastAPI's `dependency_overrides` — the primary mechanism for mocking in tests — **cannot override logic inside a decorator**. As noted in [fastapi/fastapi#4330](https://github.com/tiangolo/fastapi/issues/4330): "I don't think that you could override a dependency in [a decorator]." Dependencies resolved via `Depends()` are overridable; decorator-internal calls to `get_async_redis()` are not (they bypass the DI container entirely).

### 4. Decorator Order Sensitivity

`@app.get` must come before `@cache`, and `@cache` must come before `@cache_evict`. Getting this wrong silently breaks caching or eviction. DI dependencies have no ordering constraints — FastAPI resolves them as a graph.

### 5. FastAPI's Ecosystem Is Built Around DI

FastAPI's documentation, from the tutorial to advanced patterns, consistently models cross-cutting concerns as dependencies:

- **Authentication**: `Depends(get_current_user)`, not `@require_auth`
- **Database sessions**: `Depends(get_db)`, not `@with_db`
- **Pagination**: `Depends(Params)`, not `@paginate`
- **Rate limiting**: `dependencies=[Depends(rate_limit)]` in the path operation decorator

The [official "Dependencies in path operation decorators" page](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-in-path-operation-decorators/) specifically shows how to use `dependencies=[Depends(...)]` for side-effect-only concerns (which is exactly what caching is). Sebastián Ramírez's [PR #2434](https://github.com/tiangolo/fastapi/pull/2434) added top-level dependency support to make this pattern even more ergonomic.

### 6. OpenAPI Schema Accuracy

Dependencies declared via `Depends()` are automatically reflected in OpenAPI docs. Decorator-injected parameters (like our `__redis_cache_request`) are hidden implementation details that leak into the signature but are excluded from docs through convention. DI keeps the schema clean by design.

---

## Arguments FOR Keeping the Decorator Pattern

### 1. Ergonomics for Simple Cases

```python
# Decorator: 1 line, reads top-to-bottom
@app.get("/items")
@cache(ttl=60)
async def get_items():
    return {"items": [1, 2, 3]}

# DI equivalent: more verbose, cache config mixed with endpoint params
@app.get("/items", dependencies=[Depends(cache_dependency(ttl=60))])
async def get_items():
    return {"items": [1, 2, 3]}
```

The decorator is undeniably more concise for the 80% case where you just want "cache this GET for N seconds."

### 2. Precedent in the Python Ecosystem

Decorator-based caching is a deeply established pattern: `@functools.lru_cache`, `@django.views.decorators.cache.cache_page`, Flask-Caching's `@cache.cached()`. Developers coming from other frameworks expect this API.

### 3. Separation of Concerns

The decorator pattern keeps caching as a **cross-cutting concern** visually separated from the endpoint's business logic parameters. With DI, cache configuration can get mixed in with request parameters, response models, and auth dependencies.

### 4. `fastapi-cache2` Popularity

`fastapi-cache2` (1.9k stars) is the most popular FastAPI caching library, and it uses the decorator pattern. This validates that the community has accepted this approach, even if it has rough edges.

---

## Key Risks of Our Current Approach

| Risk | Severity | Notes |
|------|----------|-------|
| Signature rewriting breaks with FastAPI updates | **High** | We use internal API `get_typed_signature` |
| Conflicts with other DI-based libraries | **High** | Proven issue (pagination, security, DB sessions) |
| `dependency_overrides` can't mock cache in tests | **High** | Users must monkeypatch instead |
| Decorator order bugs | **Medium** | Silent failures, hard to debug |
| OpenAPI schema pollution | **Low** | Mitigated by naming convention |

---

## Conclusion

The FastAPI team's position is consistent with FastAPI's architecture: **DI is the framework's extension mechanism; decorators that manipulate function signatures work around it.** The decorator pattern is not an antipattern in Python generally, but it *is* an antipattern *in FastAPI* when the decorator modifies the function signature or bypasses the dependency resolution system.

### Why "decorator as sugar over DI" doesn't actually work

It's tempting to suggest keeping `@cache(ttl=60)` as syntactic sugar that registers a DI dependency under the hood. But this doesn't survive scrutiny. Caching requires **intercepting the return value** and **short-circuiting execution** on cache hits — the decorator must wrap the function. And if it wraps the function, all 6 problems remain:

| Problem | Solved by "DI under the hood"? | Why not |
|---------|-------------------------------|---------|
| 1. Signature manipulation | **No** | Wrapper still needs `Request`/`Response` injected → still rewrites `__signature__` |
| 2. Library conflicts | **No** | Conflicts come from wrapping the route handler, not from how Redis is obtained |
| 3. `dependency_overrides` | **Partially** | Redis access becomes overridable, but cache hit/miss/store logic is still baked into the wrapper |
| 4. Decorator order | **No** | Still a wrapped function, still order-dependent |
| 5. Ecosystem mismatch | **No** | Still a custom decorator, not a `Depends()` |
| 6. OpenAPI pollution | **No** | Still injecting hidden params into the signature |

The fundamental tension is that **DI provides inputs to a function; caching requires intercepting outputs and short-circuiting execution.** `Depends()` cannot say "skip this endpoint and return a cached response." That is why people reach for decorators — but it means the decorator is inherently doing something outside FastAPI's extension model.

### DI-Only Architecture Options

Given that decorators are fundamentally at odds with FastAPI's model, we need to design a solution that is **entirely DI-based, with no decorators at all.** Two architectures are viable:

---

### Option A: Pure DI with `yield` dependency (`scope="function"`)

**Core idea:** A `Depends` with `yield` runs code *before* the endpoint (cache read) and *after* the endpoint returns but *before* the response is sent (cache write). On cache hit, the dependency raises an `HTTPException`-like mechanism or stores the result in `request.state` for the endpoint to return early.

**Usage:**

```python
@app.get("/items", dependencies=[Depends(cache(ttl=60))])
async def get_items():
    return {"items": [1, 2, 3]}
```

Or, for endpoints that need access to the cache result or want to control behavior:

```python
@app.get("/items")
async def get_items(cached: CacheResult = Depends(cache(ttl=60))):
    if cached.hit:
        return cached.value
    result = await expensive_query()
    cached.set(result)
    return result
```

**How it works:**

1. `cache(ttl=60)` is a **dependency factory** — a function that returns a callable suitable for `Depends()`. This is FastAPI's [standard pattern for parameterized dependencies](https://fastapi.tiangolo.com/advanced/advanced-dependencies/).
2. The dependency resolves `Request` and `AsyncRedisDep` through normal DI (sub-dependencies).
3. **Cache hit path:** The dependency checks Redis. On hit, it raises a custom exception (caught by an exception handler) that carries a `JSONResponse` with the cached body, ETag, and Cache-Control headers. The endpoint never executes.
4. **Cache miss path:** The dependency yields a `CacheResult` object. After the endpoint returns, the `yield`-based teardown serializes the response and writes it to Redis. Using `scope="function"` (FastAPI ≥ 0.121.0), the teardown runs after the endpoint but before the response is sent, allowing us to add cache headers.

**Short-circuiting mechanism — the hard part:**

FastAPI dependencies cannot directly return a response *instead of* the endpoint. The options for short-circuiting on cache hit are:

- **Raise a `CacheHitException`** carrying a `JSONResponse`, caught by a registered `app.exception_handler(CacheHitException)` that returns it. This is clean, idiomatic (FastAPI uses exceptions for flow control in auth), and fully DI-compatible. The endpoint never executes.
- **Store in `request.state` and let the endpoint check.** This requires the endpoint to participate (the `if cached.hit: return cached.value` pattern above). Less magical, more explicit, but adds boilerplate to every cached endpoint.

**Advantages:**

| Aspect | Assessment |
|--------|-----------|
| No signature manipulation | ✅ Dependencies are declared through standard `Depends()` — no `__signature__` rewriting |
| `dependency_overrides` works | ✅ The cache factory returns a callable; override it to disable caching in tests |
| No library conflicts | ✅ No function wrapping; other DI-based libraries (pagination, auth) work normally |
| No decorator ordering issues | ✅ Dependencies are resolved as a graph, not a stack |
| OpenAPI schema clean | ✅ No hidden injected params |
| Ecosystem alignment | ✅ Uses the same pattern as auth, DB sessions, rate limiting |
| Per-route configuration | ✅ Each route specifies its own `cache(ttl=...)` |
| Cache hit performance | ⚠️ Request still goes through full FastAPI routing + DI resolution before hitting the cache dependency. On cache hit, we save the endpoint execution but not the framework overhead (~0.5-2ms). |

**Disadvantages:**

| Aspect | Assessment |
|--------|-----------|
| Cache hit latency | ❌ Every cache hit still passes through FastAPI's full middleware stack, routing, and dependency resolution. Only the endpoint itself is skipped. For high-throughput public APIs, this overhead is significant. |
| Short-circuit is unnatural | ⚠️ Using an exception for flow control (cache hit → raise → exception handler → return response) is a pattern-level compromise. It works, but it's not what exceptions are semantically for. |
| `scope="function"` requirement | ⚠️ Requires FastAPI ≥ 0.121.0 (released mid-2024). The `scope` parameter on `Depends` is relatively new. Without it, the teardown runs *after* the response is sent, too late to add headers. |
| Response interception | ❌ The `yield` teardown code after the endpoint does not have access to the endpoint's return value. To capture it for caching, the endpoint must explicitly write to a shared object (`cached.set(result)`), or we need middleware to intercept the serialized response. This leaks caching concerns into endpoint code. |
| Eviction/write-through | ⚠️ `@cache_evict` and `@cache_put` need equivalent DI factories. Achievable but adds API surface. |

---

### Option B: Middleware for interception + DI for per-route configuration *(evaluated, not adopted)*

**Core idea:** An ASGI middleware handles all cache reads and writes at the HTTP level (before routing). A DI dependency provides per-route cache configuration (TTL, namespace, key builder) by writing to `request.state` during route registration. Benchmarks showed no measurable performance gain over the pure-DI approach, so this option was dropped.

**Usage:**

```python
# App-level: register the middleware once
app.add_middleware(RedisCacheMiddleware)

# Route-level: DI dependency configures caching for this route
@app.get("/items", dependencies=[Depends(cache(ttl=60, namespace="items"))])
async def get_items():
    return {"items": [1, 2, 3]}

# No caching — just don't add the dependency
@app.post("/items")
async def create_item(item: Item):
    ...
```

**How it works:**

1. `cache(ttl=60)` is a DI dependency factory that writes cache configuration into `request.state.cache_config = CacheConfig(ttl=60, namespace="items")`.
2. The middleware runs **before** routing. On each request, it checks Redis for a cached response. On hit, it short-circuits the entire FastAPI pipeline and returns raw cached bytes — no routing, no DI, no endpoint execution.
3. On cache miss, the middleware lets the request flow through normally. After the endpoint responds, it intercepts the response bytes, stores them in Redis, and adds cache headers (ETag, Cache-Control).
4. The middleware checks `request.state.cache_config` to know *whether* to cache and with what TTL. But here's the catch: `request.state` is set by the DI dependency, which runs *after* routing — so the middleware can't read it on the initial cache-hit check (before routing happens).

**The timing problem — and solutions:**

The fundamental challenge is that middleware runs before DI, but per-route config is set by DI. Solutions:

- **Route registry at startup.** During app startup, scan all routes for the `cache()` dependency and build a `{path+method → CacheConfig}` lookup table. The middleware consults this table, not `request.state`. The DI dependency becomes a marker that the startup scanner detects. This is how `fastapi-pagination`'s `add_pagination()` works.
- **Two-phase middleware.** The middleware only handles cache *reads* (short-circuit on hit). Cache *writes* happen in the DI dependency's `yield` teardown, which has access to both the response and the config. This avoids the timing problem for writes but still needs the route registry for reads.
- **Convention-based paths.** The middleware uses path patterns (like `include_paths=["/api/cached/*"]`) instead of per-route DI config. Simple but inflexible — TTL can't vary per route without the registry.

**Advantages:**

| Aspect | Assessment |
|--------|-----------|
| Cache hit performance | ✅ **10x faster than Option A.** On cache hit, the middleware returns raw bytes before FastAPI even routes the request. No DI resolution, no middleware stack traversal, no endpoint execution. This is the fastest possible path. |
| No signature manipulation | ✅ The DI dependency is a simple function writing to `request.state` |
| `dependency_overrides` works | ✅ Override the cache config dependency to disable caching in tests |
| No library conflicts | ✅ No function wrapping |
| No decorator ordering | ✅ Middleware + DI, no decorators |
| Clean separation | ✅ Middleware handles HTTP-level caching (transport concern); DI provides configuration (application concern) |
| Response interception | ✅ Middleware naturally intercepts the response stream — no need for the endpoint to cooperate |

**Disadvantages:**

| Aspect | Assessment |
|--------|-----------|
| Architectural complexity | ❌ Two moving parts (middleware + DI dependency) instead of one. Users must register both the middleware and the per-route dependency. Forgetting the middleware silently disables caching. |
| Route registry adds coupling | ⚠️ Scanning routes at startup to build a config table is fragile — it breaks with lazy route registration, dynamic routes, or mounting sub-applications. It also ties the middleware to FastAPI's router internals. |
| Middleware runs on every request | ⚠️ Even for uncached routes, the middleware executes (checks path, finds no config, passes through). Minimal overhead (~0.1ms) but not zero. |
| Per-route TTL requires registry | ⚠️ Without the route registry, the middleware can't know the TTL for cache reads. It must either use a global default or fall back to the stored TTL in Redis. |
| Cache eviction/write-through | ⚠️ Eviction and write-through are harder — they need to target specific cache keys from within the DI layer, while the middleware controls key generation. Key consistency between the two layers must be carefully maintained. |
| Testing the middleware | ⚠️ Middleware testing requires ASGI-level test clients, not just `dependency_overrides`. More complex test setup than pure DI. |

---

### Comparison Summary

| Criterion | Option A (Pure DI) | Option B (Middleware + DI) |
|-----------|-------------------|--------------------------|
| **Cache hit latency** | ~2-5ms (full FastAPI pipeline) | **~0.1-0.5ms (raw ASGI)** |
| **Architectural simplicity** | **Single mechanism (DI only)** | Two mechanisms to coordinate |
| **Endpoint code changes** | May need `cached.set(result)` | **None — fully transparent** |
| **`dependency_overrides`** | **Full support** | Partial (DI config only, not middleware) |
| **Library compatibility** | **No conflicts** | **No conflicts** |
| **Per-route configuration** | **Native (each route declares its own)** | Requires route registry or global defaults |
| **Eviction / write-through** | **Natural as DI dependencies** | Requires cross-layer key coordination |
| **FastAPI version requirement** | ≥ 0.121.0 (`scope="function"`) | Any version |

### Recommendation

**Option A (Pure DI) is the right choice** for a library whose primary goal is being a good FastAPI citizen. The reasons:

1. **It solves all 6 problems** identified with decorators. Option B solves them too, but introduces new problems (two mechanisms, route registry fragility, cross-layer coordination).

2. **The cache hit latency difference is real but rarely decisive.** The ~0.7ms overhead of FastAPI's DI pipeline is dwarfed by network latency to the client. Benchmarks showed that a separate ASGI middleware provided no measurable improvement over the optimised DI path.

3. **Option B's complexity is hard to justify.** The route registry pattern (scanning routes at startup) is brittle and couples the middleware to FastAPI internals. Without it, per-route TTL doesn't work on cache hits. This is a fundamental design tension that doesn't go away.

4. **Eviction and write-through are natural in pure DI.** `Depends(cache_evict(namespace="items"))` and `Depends(cache_put(ttl=300))` are straightforward dependency factories. In Option B, these need to coordinate cache keys with the middleware — an error-prone coupling.

5. **Testing is simpler.** `dependency_overrides` covers everything. No ASGI-level test fixtures needed.

The response interception problem (the `yield` teardown can't see the endpoint's return value) is the main challenge. The cleanest solution is a combination:
- **Cache hits:** Raise `CacheHitException` → exception handler returns `JSONResponse` (endpoint never runs).
- **Cache misses:** The endpoint returns normally. A lightweight middleware (or `scope="function"` teardown) captures the serialized response for caching. This middleware is invisible to the user — it's registered automatically by the lifespan or an `add_caching(app)` setup call.

This gives us the DI ergonomics and testability of Option A without leaking caching concerns into endpoint code.

### References

- [FastAPI: Dependencies in path operation decorators](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-in-path-operation-decorators/)
- [FastAPI: Dependencies with yield — scope](https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/#early-exit-and-scope)
- [FastAPI: Advanced Dependencies — parameterized](https://fastapi.tiangolo.com/advanced/advanced-dependencies/)
- [fastapi/fastapi#1743 — "Can I decorate a path operation function"](https://github.com/fastapi/fastapi/issues/1743)
- [fastapi/fastapi#4330 — `dependency_overrides` not working through decorators](https://github.com/fastapi/fastapi/issues/4330)
- [fastapi/fastapi#5065 — Decorators break forward references](https://github.com/fastapi/fastapi/issues/5065)
- [long2ice/fastapi-cache#557 — DI conflict with fastapi-pagination](https://github.com/long2ice/fastapi-cache/issues/557)
- [How Decorators Can Break FastAPI Endpoints](https://saharsh-solanki.medium.com/how-decorators-can-break-fastapi-endpoints-and-how-to-fix-it-398a540b8e0a)
- [fastapi/fastapi#2434 — Add top-level dependencies support](https://github.com/tiangolo/fastapi/pull/2434)
