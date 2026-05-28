# OpenAPI Cache Documentation Research

## Problem Statement

`redis-fastapi` injects cache-related behavior at runtime — response headers (`X-Redis-Cache`, `ETag`, `Cache-Control`), `304 Not Modified` responses, and `Cache-Control` request header handling — but none of this is visible in the generated OpenAPI schema. Users' API consumers looking at `/docs` have no idea caching is involved.

Specifically, what's invisible:

1. **Response headers** — `X-Redis-Cache` (HIT/MISS), `ETag`, `Cache-Control` are added by `CacheResponseCaptureMiddleware` but never declared in the schema.
2. **304 Not Modified** — `cache()` returns this on ETag match, but endpoints don't declare it as a possible response.
3. **Request headers** — The library honors `Cache-Control: no-cache`, `no-store`, `max-age=N`, and `If-None-Match`, but none appear as documented parameters.
4. **Complete invisibility** — The `dependencies=[Depends(cache(...))]` pattern leaves zero trace in the OpenAPI schema.

## How the OpenAPI Spec Handles Caching

OpenAPI supports response headers natively — you can declare `Cache-Control`, `ETag`, etc. per response status code. However, there is an [open issue (OAI #2784)](https://github.com/OAI/OpenAPI-Specification/issues/2784) targeted at v3.3.0 acknowledging that `Cache-Control` is awkward to model because it's a comma-separated list of directives, not a simple string. The current workaround is `schema: { type: string }` with an example value.

Response headers are declared per-response in the spec:

```yaml
responses:
  200:
    description: Successful response
    headers:
      Cache-Control:
        schema:
          type: string
          example: "max-age=300"
      ETag:
        schema:
          type: string
          example: 'W/"abc123"'
      X-Redis-Cache:
        schema:
          type: string
          enum: [HIT, MISS]
  304:
    description: Not Modified (ETag match)
```

## How Other Frameworks/Libraries Handle This

### fastapi-cache2 (Python, long2ice) — Does Not Address It

The most popular FastAPI caching library does not solve this problem and has open issues about it:

- [#384](https://github.com/long2ice/fastapi-cache/issues/384): The `@cache` decorator actually *breaks* OpenAPI by replacing the function signature, losing the `response_model`.
- [#498](https://github.com/long2ice/fastapi-cache/issues/498): No way to customize `Cache-Control` header values.
- Injects `X-FastAPI-Cache`, `ETag`, and `Cache-Control` headers at runtime but they are invisible in the schema.

### NestJS @nestjs/cache-manager (Node.js) — Manual Decorators

The `CacheInterceptor` adds `X-Cache: HIT/MISS` at runtime, but OpenAPI documentation is a completely separate concern. Users must manually add Swagger decorators (`@ApiHeader`, `@ApiResponse`) alongside cache decorators. No automatic schema enrichment.

### Spring Boot / springdoc-openapi (Java) — Annotation-Based

Spring uses `@Operation` and `@ApiResponse` annotations from `swagger-annotations`. Cache behavior (typically via `@Cacheable`) is orthogonal to OpenAPI annotations. Developers manually annotate response headers. No automatic bridging between cache config and OpenAPI schema.

### WSO2 API Gateway — Vendor Extensions

Uses `x-wso2-response-cache` vendor extension in the OpenAPI definition to configure gateway-level response caching. This is consumed by their tooling but invisible to standard OpenAPI renderers.

## Possible Approaches for redis-fastapi

### Tier 1: Export Reusable `responses` Dicts (Recommended)

FastAPI's `responses=` parameter on routes and `APIRouter` supports merging predefined response definitions. This is explicitly documented as a [recommended pattern](https://fastapi.tiangolo.com/advanced/additional-responses/#combine-predefined-responses-and-custom-ones).

```python
from redis_fastapi import cache, CACHE_RESPONSES

@app.get("/items",
    dependencies=[Depends(cache(ttl=300))],
    responses={**CACHE_RESPONSES},
)
async def get_items():
    return {"items": [1, 2, 3]}
```

The library would export:

- `CACHE_RESPONSES` — documents 304 + cache headers (ETag, Cache-Control, X-Redis-Cache) on 200
- `CACHE_EVICT_RESPONSES` — simpler version without 304/ETag (for evict/put endpoints)

**Pros:** Idiomatic FastAPI, zero magic, composable, users opt in explicitly.
**Cons:** Requires one extra line per endpoint.

### Tier 2: OpenAPI Vendor Extensions (`x-cache-*`)

Add `x-cache-ttl`, `x-cache-namespace`, etc. to operation metadata via a custom `openapi()` override. Similar to AWS API Gateway and WSO2 patterns.

**Pros:** Machine-readable cache metadata for tooling.
**Cons:** Limited tooling support; invisible in Swagger UI / ReDoc.

### Tier 3: Automatic Schema Enrichment

Override `app.openapi()` to walk all routes, detect which ones have cache dependencies, and inject response headers and 304 response into the schema automatically.

**Pros:** Zero manual work for users.
**Cons:** Fragile — requires introspecting FastAPI internals to detect dependencies from route metadata; opaque behavior; harder to test and maintain.

## Recommendation

**Tier 1 (reusable `responses` dicts)** is the pragmatic choice:

- It is idiomatic FastAPI and follows the framework's own documented patterns.
- No other Python caching library does even this much — it would be a differentiator.
- It is composable — users can merge with their own `responses` declarations.
- It avoids fragile introspection of FastAPI internals.

Tier 2 (vendor extensions) could be layered on later for tooling consumers. Tier 3 (auto-enrichment) is not recommended due to maintenance burden and opacity.
