# Installation

=== "pip"

    ```bash
    pip install redis-fastapi
    ```

=== "uv"

    ```bash
    uv add redis-fastapi
    ```

=== "poetry"

    ```bash
    poetry add redis-fastapi
    ```

## Requirements

| Dependency          | Supported versions | Notes                   |
|---------------------|--------------------|-------------------------|
| Python              | >= 3.10            |                         |
| `redis` (redis-py)  | >= 6.0             | Installed automatically |
| `fastapi`           | >= 0.115           | Installed automatically |
| `pydantic`          | >= 2.0             | Installed automatically |
| Redis server        | >= 7.4             | Required at runtime     |

A running Redis server (or [Redis Cloud](https://redis.io/cloud/) instance) is
required at runtime.

