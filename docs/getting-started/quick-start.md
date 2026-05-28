# Quick Start

## Create the app



```python
from fastapi import Depends, FastAPI
from redis_fastapi import FastAPIRedis, cache

app = FastAPI()

# Set up connection pool (lifespan) and caching middleware
FastAPIRedis(app).lifespan().caching()

# Depends(cache()) caches the GET response in Redis
@app.get("/items", dependencies=[Depends(cache())])
async def get_items():
    return {"items": [1, 2, 3]}
```

- **`FastAPIRedis(app)`** — builder that wires Redis into the app.
- **`.lifespan()`** — manages a shared async connection pool
  (startup → create pool, shutdown → close).  Wraps any existing
  lifespan, so it composes with other libraries.
- **`.caching()`** — registers the exception handler and capture
  middleware needed by the `cache()` / `cache_evict()` / `cache_put()`
  dependency factories.
- **`Depends(cache())`** — on cache hit the endpoint is skipped entirely;
  on miss the response is stored in Redis for subsequent requests.

## Configure Redis

Point the library at your Redis server with a `.env` file or
environment variables:

=== ".env file"

    ```dotenv
    REDIS_URL=redis://localhost:6379/0
    ```

=== "Environment variable"

    ```bash
    export REDIS_URL=redis://localhost:6379/0
    ```

Then start the app:

```bash
uvicorn myapp:app
```

See [Configuration](../guide/configuration.md) for all options.

