# Cache interception points in FastAPI

This page explains where caching logic can hook into a FastAPI request and
why redis-fastapi uses the combination it does.

---

## Request lifecycle

```mermaid
sequenceDiagram
    participant C as Client
    participant MW as ASGI Middleware
    participant R as Router / APIRoute
    participant DI as Dependency Injection
    participant EP as Endpoint function
    participant S as Serialization (JSONResponse)

    C->>MW: HTTP request

    Note over MW: ① Middleware (before)<br/>Sees raw ASGI scope.<br/>Can short-circuit here<br/>(no access to route config).

    MW->>R: scope, receive, send

    Note over R: ② APIRoute.get_route_handler()<br/>Wraps the endpoint callable.<br/>Sees the Response object<br/>before ASGI serialization.

    R->>DI: resolve dependencies

    Note over DI: ③ Dependency Injection<br/>Depends(cache(ttl=60))<br/>Has Request, Redis client,<br/>per-route config.<br/>Can short-circuit (raise).<br/>Cannot see return value.

    DI->>EP: call endpoint
    EP-->>DI: return value (Python object)
    DI-->>R: return value

    Note over R: ② APIRoute (after)<br/>Receives the Response object.<br/>Can pass body through a Coder.

    R->>S: build Response
    S-->>MW: ASGI response messages

    Note over MW: ① Middleware (after)<br/>Sees raw body bytes.<br/>Can buffer and store in Redis.<br/>No access to Coder or route config.

    MW-->>C: HTTP response
```

## How redis-fastapi uses these hooks

| Concern | Hook | Why |
|---|---|---|
| Cache **read** + short-circuit on hit | **③ DI** — `Depends(cache(ttl=60))` | Full access to `Request`, Redis client, per-route config. `dependency_overrides` works for testing. Short-circuits by raising `CacheHitException`. |
| Cache **write** on miss | **① Middleware** — `CacheResponseCaptureMiddleware` | Runs after the response is fully serialized. Registered once by `add_redis_caching(app)`, transparent to the user. |

---

## Why not use APIRoute for the write path?

A custom `APIRoute` subclass (hook ②) would give the write path access to
the `Response` object and the `Coder`, solving the binary-body problem that
the middleware currently has. However, `route_class` is consumed at **route
registration time** — it determines which `Route` subclass wraps each
endpoint when `@app.get(...)` is evaluated.

This creates an unsolvable ordering problem for auto-configuration:

- **`app.router.route_class = CachedRoute`** only affects routes registered
  *after* the call. The natural FastAPI pattern is to define routes first
  and call builder methods (like `FastAPIRedis(app).caching()`) second, so
  existing routes would not be wrapped.
- **Retroactively walking `app.routes`** to patch already-registered routes
  requires reaching into Starlette's internal `Route.app` / `Route.endpoint`
  attributes — undocumented and fragile.
- **Requiring the builder call before route registration** breaks the
  standard FastAPI setup order and surprises users.

ASGI middleware does not have this problem. It wraps the entire application
at the outermost layer regardless of when routes are registered or how
sub-applications are mounted. This is why the miss-path write uses
middleware despite its limitations (no access to the `Coder`, body stored
as a JSON string rather than passed through the user's serializer).
