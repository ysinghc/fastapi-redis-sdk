# Pydantic Settings Pattern Analysis

## Question

Is the current configuration based on [FastAPI's Settings guide](https://fastapi.tiangolo.com/advanced/settings), and if not, do we benefit from implementing it that way?

---

## Current Implementation vs FastAPI Pattern

### What We Have Now

```python
# src/redis_fastapi/config.py
from dataclasses import dataclass

@dataclass
class RedisSettings:
    """Central configuration for the Redis FastAPI integration."""
    
    url: str | None = None
    host: str = "localhost"
    port: int = 6379
    # ... etc
    
    @classmethod
    def from_env(cls) -> RedisSettings:
        """Build settings from environment variables."""
        url = os.getenv("REDIS_URL")
        return cls(
            url=url if url else None,
            host=os.getenv("REDIS_HOST", "localhost"),
            # ... manual parsing
        )

# Module-level instance
settings = RedisSettings.from_env()
```

**Approach**: Manual `dataclass` + `os.getenv()` + module-level singleton

---

### FastAPI Recommended Pattern

```python
# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "Awesome API"
    admin_email: str
    items_per_user: int = 50
    
    model_config = SettingsConfigDict(env_file=".env")

# Dependency with lru_cache
from functools import lru_cache

@lru_cache
def get_settings():
    return Settings()

# Usage in endpoints
@app.get("/info")
async def info(settings: Annotated[Settings, Depends(get_settings)]):
    return {"app_name": settings.app_name}
```

**Approach**: `pydantic-settings` + dependency injection + `@lru_cache`

---

## Comparison

| Aspect | Current (dataclass) | FastAPI Pattern (Pydantic Settings) |
|--------|---------------------|-------------------------------------|
| **Dependency** | None (uses `pydantic` only) | `pydantic-settings` (extra package) |
| **Validation** | Manual type hints | Automatic Pydantic validation |
| **Env parsing** | Manual `os.getenv()` | Automatic with type coercion |
| **.env support** | Manual implementation needed | Built-in with `env_file` |
| **Type coercion** | Manual (`int(os.getenv(...))`) | Automatic (str → int/bool/etc.) |
| **Error messages** | Generic Python errors | Rich Pydantic validation errors |
| **Testing** | Modify module-level `settings` | Easy dependency override |
| **Hot reload** | Module-level singleton | `@lru_cache` with same effect |
| **Nested models** | Not supported | Pydantic models |
| **Secrets** | No special handling | Can use `SecretStr` |

---

## Analysis

### What We're Missing

#### 1. **Automatic Type Coercion**

**Current** (manual):
```python
port=int(os.getenv("REDIS_PORT", "6379"))
ssl=os.getenv("REDIS_SSL", "").lower() in ("1", "true", "yes")
```

**Pydantic Settings** (automatic):
```python
class Settings(BaseSettings):
    port: int = 6379  # Automatically converts str → int
    ssl: bool = False  # Automatically converts "true"/"1" → True
```

#### 2. **Better Error Messages**

**Current**:
```python
ValueError: invalid literal for int() with base 10: 'abc'
```

**Pydantic**:
```
ValidationError: 1 validation error for Settings
port
  value is not a valid integer (type=type_error.integer)
```

#### 3. **.env File Support**

**Current**: Not supported (users must manually load .env)

**Pydantic Settings**:
```python
model_config = SettingsConfigDict(env_file=".env")
# Automatically reads from .env file
```

#### 4. **Testability**

**Current** (harder):
```python
# Must modify module-level variable
from redis_fastapi import settings
original_host = settings.host
settings.host = "test-redis"  # Modifies global state
# ... test ...
settings.host = original_host  # Reset
```

**Pydantic Settings** (easier):
```python
# Override dependency
def get_settings_override():
    return Settings(host="test-redis")

app.dependency_overrides[get_settings] = get_settings_override
```

#### 5. **Secrets Management**

**Current**: Passwords stored as plain `str`

**Pydantic Settings**:
```python
from pydantic import SecretStr

class Settings(BaseSettings):
    password: SecretStr  # Won't print in logs/repr
```

---

## Should We Change?

### Pros of Switching to Pydantic Settings

✅ **Better validation** - Automatic type coercion + rich error messages  
✅ **.env support** - Built-in dotenv loading  
✅ **Easier testing** - Dependency injection pattern  
✅ **Secrets handling** - `SecretStr` for passwords  
✅ **Consistent with FastAPI** - Follows official recommendations  
✅ **Less code** - No manual `from_env()` method  
✅ **Better DX** - Better error messages for misconfiguration

### Cons of Switching

❌ **Extra dependency** - Adds `pydantic-settings` requirement  
❌ **Breaking change** - Module-level `settings` → dependency function  
❌ **Migration effort** - Users must update their code  
❌ **Not urgent** - Current approach works fine

---

## Recommendation

### **Yes, we should adopt Pydantic Settings, but gradually**

**Why**: Better validation, .env support, and consistency with FastAPI best practices

**How**: Implement as **v0.2.0 or v0.3.0** (not urgent)

### Migration Path

#### Phase 1: Make Current Code Compatible (v0.2.0)

Keep current `dataclass` approach but add Pydantic Settings as **optional**:

```python
# config.py
from dataclasses import dataclass

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
    PYDANTIC_SETTINGS_AVAILABLE = True
except ImportError:
    PYDANTIC_SETTINGS_AVAILABLE = False

if PYDANTIC_SETTINGS_AVAILABLE:
    # New Pydantic-based settings
    class RedisSettings(BaseSettings):
        url: str | None = None
        host: str = "localhost"
        port: int = 6379
        # ... etc
        
        model_config = SettingsConfigDict(
            env_prefix="REDIS_",
            env_file=".env",
            env_file_encoding="utf-8",
        )
else:
    # Fallback to dataclass (backward compatible)
    @dataclass
    class RedisSettings:
        # ... current implementation
```

**Benefits**:
- ✅ Backward compatible
- ✅ No breaking changes
- ✅ Users can opt-in by installing `pydantic-settings`

#### Phase 2: Deprecate Dataclass (v0.3.0)

Add deprecation warning:

```python
import warnings

if not PYDANTIC_SETTINGS_AVAILABLE:
    warnings.warn(
        "Using dataclass-based settings is deprecated. "
        "Install pydantic-settings: pip install pydantic-settings",
        DeprecationWarning,
    )
```

#### Phase 3: Remove Dataclass (v1.0.0)

Make `pydantic-settings` required dependency.

---

## Proposed Implementation (Pydantic Settings)

```python
# src/redis_fastapi/config.py
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class RedisSettings(BaseSettings):
    """Central configuration for the Redis FastAPI integration.
    
    Environment variables should be prefixed with REDIS_
    Example: REDIS_URL, REDIS_HOST, REDIS_PORT, etc.
    """
    
    # Connection: URL mode
    url: str | None = None
    
    # Connection: KV mode
    host: str = "localhost"
    port: int = Field(default=6379, ge=1, le=65535)  # Port validation
    db: int = Field(default=0, ge=0, le=15)  # Database validation
    username: str | None = None
    password: SecretStr | None = None  # Secret handling
    
    # TLS
    ssl: bool = False
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    ssl_ca_certs: str | None = None
    ssl_check_hostname: bool = False
    
    # Pool
    max_connections: int | None = Field(default=None, ge=1)
    socket_timeout: float | None = Field(default=None, ge=0)
    socket_connect_timeout: float | None = Field(default=None, ge=0)
    
    # Cluster
    cluster: bool = False
    
    # Caching
    prefix: str = "redis:fastapi"
    default_ttl: int = Field(default=60, ge=0)
    
    model_config = SettingsConfigDict(
        env_prefix="REDIS_",  # All env vars start with REDIS_
        env_file=".env",      # Automatically read from .env
        env_file_encoding="utf-8",
        case_sensitive=False,  # REDIS_URL = redis_url = REDIS_url
    )
    
    # Keep existing helper methods
    def pattern_prefix(self, pattern: str) -> str:
        return f"{self.prefix}:{pattern}"
    
    def connection_kwargs(self) -> dict:
        # ... same as before
        pass

# Dependency function with caching
from functools import lru_cache

@lru_cache
def get_settings() -> RedisSettings:
    """Get cached settings instance."""
    return RedisSettings()

# Module-level instance for backward compatibility
settings = get_settings()
```

### Usage

**Current code still works**:
```python
from redis_fastapi import settings

print(settings.host)  # Works
```

**New dependency injection pattern** (optional):
```python
from redis_fastapi import get_settings
from fastapi import Depends

@app.get("/config")
async def show_config(settings: Annotated[RedisSettings, Depends(get_settings)]):
    return {"host": settings.host}
```

---

## Benefits Summary

### Immediate Benefits (v0.2.0)

1. ✅ **Better validation**: Port must be 1-65535, DB must be 0-15
2. ✅ **.env support**: Users can create `.env` files
3. ✅ **Better errors**: "port: value is not a valid integer" vs "ValueError"
4. ✅ **Type safety**: Automatic str→int/bool conversion
5. ✅ **Secrets**: Passwords won't print in logs

### Long-term Benefits (v1.0.0)

6. ✅ **Easier testing**: Dependency injection pattern
7. ✅ **Consistent with FastAPI**: Follows official guide
8. ✅ **Less maintenance**: No manual `from_env()` method
9. ✅ **Better DX**: Users get instant feedback on misconfiguration

---

## Recommendation

**Adopt Pydantic Settings in v0.2.0 as optional, make required in v1.0.0**

**Implementation priority**: Medium (after cache metrics, pattern clearing)

**Breaking change**: No (keep backward compatibility)

**User impact**: Positive (better validation, .env support, easier testing)

---

## Next Steps

1. Add `pydantic-settings` as **optional** dependency in `pyproject.toml`
2. Implement Pydantic-based `RedisSettings` with fallback
3. Add `.env` file example to documentation
4. Update testing guide to show dependency override pattern
5. Deprecate dataclass approach in v0.3.0
6. Make `pydantic-settings` required in v1.0.0

---

**Conclusion**: Yes, we should adopt the FastAPI pattern. It provides significant benefits with minimal downside, and we can do it gradually without breaking changes.
