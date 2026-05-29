"""Shared types for fastapi-redis-sdk."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeAlias, runtime_checkable


@runtime_checkable
class Coder(Protocol):
    """Protocol for encoding/decoding cached values."""

    @classmethod
    def encode(cls, value: Any) -> str: ...  # pragma: no cover

    @classmethod
    def decode(cls, value: str) -> Any: ...  # pragma: no cover


class JsonCoder:
    """Default JSON coder using stdlib json."""

    @classmethod
    def encode(cls, value: Any) -> str:
        return json.dumps(value)

    @classmethod
    def decode(cls, value: str) -> Any:
        return json.loads(value)


# A key builder receives (request, eviction_group, prefix) and returns a cache key.
KeyBuilder: TypeAlias = Callable[..., str | Awaitable[str]]
