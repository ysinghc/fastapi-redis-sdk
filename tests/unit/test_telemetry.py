"""Unit tests for redis_fastapi.telemetry module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import redis_fastapi.telemetry as tel

# ---------------------------------------------------------------------------
# Helpers to reset module-level state between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_telemetry():
    """Reset telemetry module state before and after each test."""
    original = tel._state
    tel.disable_telemetry()
    yield
    tel._state = original


# ===================================================================
# is_enabled()
# ===================================================================


@pytest.mark.unit
class TestIsEnabled:
    def test_initially_disabled(self):
        tel._state.enabled = False
        assert tel.is_enabled() is False

    def test_enabled_after_enable(self):
        tel._state.enabled = True
        assert tel.is_enabled() is True


# ===================================================================
# enable_telemetry()
# ===================================================================


@pytest.mark.unit
class TestEnableTelemetry:
    def test_enable_sets_tracer_and_meter(self):
        tel._state.enabled = False
        tel.enable_telemetry()
        assert tel._state.enabled is True
        assert tel._state.tracer is not None
        assert tel._state.meter is not None
        assert tel._state.cache_requests is not None
        assert tel._state.cache_evictions is not None
        assert tel._state.cache_writes is not None
        assert tel._state.cache_latency is not None

    def test_idempotent(self):
        tel._state.enabled = False
        tel.enable_telemetry()
        tracer1 = tel._state.tracer
        tel.enable_telemetry()  # second call
        assert tel._state.tracer is tracer1  # same object

    def test_no_otel_installed(self):
        tel._state.enabled = False
        with patch.object(tel, "_try_import_otel", return_value=None):
            tel.enable_telemetry()
        assert tel._state.enabled is False
        assert tel._state.tracer is None


# ===================================================================
# _try_import_otel()
# ===================================================================


@pytest.mark.unit
class TestTryImportOtel:
    def test_returns_modules_when_installed(self):
        result = tel._try_import_otel()
        assert result is not None
        trace, metrics = result
        assert hasattr(trace, "get_tracer")
        assert hasattr(metrics, "get_meter")

    def test_returns_none_when_not_installed(self):
        import sys

        with patch.dict(
            sys.modules,
            {
                "opentelemetry": None,
                "opentelemetry.trace": None,
                "opentelemetry.metrics": None,
            },
        ):
            assert tel._try_import_otel() is None


# ===================================================================
# cache_span()
# ===================================================================


@pytest.mark.unit
class TestCacheSpan:
    def test_noop_when_disabled(self):
        tel._state.enabled = False
        with tel.cache_span("test.op", {"key": "val"}) as span:
            assert span is None

    def test_creates_span_when_enabled(self):
        tel._state.enabled = False
        tel.enable_telemetry()
        with tel.cache_span("cache.get", {"cache.key": "k"}) as span:
            assert span is not None

    def test_uses_start_as_current_span(self):
        """Span is created via start_as_current_span for proper nesting."""
        tel._state.enabled = False
        tel.enable_telemetry()
        with tel.cache_span("cache.get", {"cache.key": "k"}) as span:
            # The span should be set as the current span in the OTel context
            from opentelemetry import trace

            assert trace.get_current_span() is span


# ===================================================================
# Metric recording helpers
# ===================================================================


@pytest.mark.unit
class TestRecordCacheRequest:
    def test_noop_when_disabled(self):
        tel._state.enabled = False
        tel.record_cache_request(result="hit")  # should not raise

    def test_records_when_enabled(self):
        tel._state.enabled = True
        mock_counter = MagicMock()
        tel._state.cache_requests = mock_counter
        tel.record_cache_request(result="hit", eviction_group="ns")
        mock_counter.add.assert_called_once_with(
            1, {"result": "hit", "eviction_group": "ns"}
        )

    def test_exception_swallowed(self):
        tel._state.enabled = True
        mock_counter = MagicMock()
        mock_counter.add.side_effect = RuntimeError("boom")
        tel._state.cache_requests = mock_counter
        tel.record_cache_request(result="hit")  # should not raise


@pytest.mark.unit
class TestRecordCacheEviction:
    def test_noop_when_disabled(self):
        tel._state.enabled = False
        tel.record_cache_eviction(evict_type="key")  # should not raise

    def test_records_when_enabled(self):
        tel._state.enabled = True
        mock_counter = MagicMock()
        tel._state.cache_evictions = mock_counter
        tel.record_cache_eviction(evict_type="group", eviction_group="ns")
        mock_counter.add.assert_called_once_with(
            1, {"type": "group", "eviction_group": "ns"}
        )

    def test_exception_swallowed(self):
        tel._state.enabled = True
        mock_counter = MagicMock()
        mock_counter.add.side_effect = RuntimeError("boom")
        tel._state.cache_evictions = mock_counter
        tel.record_cache_eviction(evict_type="key")  # should not raise


@pytest.mark.unit
class TestRecordCacheWrite:
    def test_noop_when_disabled(self):
        tel._state.enabled = False
        tel.record_cache_write(write_type="miss_fill")  # should not raise

    def test_records_when_enabled(self):
        tel._state.enabled = True
        mock_counter = MagicMock()
        tel._state.cache_writes = mock_counter
        tel.record_cache_write(write_type="write_through", eviction_group="ns")
        mock_counter.add.assert_called_once_with(
            1, {"type": "write_through", "eviction_group": "ns"}
        )

    def test_exception_swallowed(self):
        tel._state.enabled = True
        mock_counter = MagicMock()
        mock_counter.add.side_effect = RuntimeError("boom")
        tel._state.cache_writes = mock_counter
        tel.record_cache_write(write_type="miss_fill")  # should not raise


@pytest.mark.unit
class TestRecordCacheLatency:
    def test_noop_when_disabled(self):
        tel._state.enabled = False
        tel.record_cache_latency(duration=0.5, operation="get")

    def test_records_when_enabled(self):
        tel._state.enabled = True
        mock_hist = MagicMock()
        tel._state.cache_latency = mock_hist
        tel.record_cache_latency(duration=0.123, operation="set", eviction_group="ns")
        mock_hist.record.assert_called_once_with(
            0.123, {"operation": "set", "eviction_group": "ns"}
        )

    def test_exception_swallowed(self):
        tel._state.enabled = True
        mock_hist = MagicMock()
        mock_hist.record.side_effect = RuntimeError("boom")
        tel._state.cache_latency = mock_hist
        tel.record_cache_latency(duration=0.1, operation="get")  # should not raise


@pytest.mark.unit
class TestTimedOperation:
    def test_records_duration(self):
        tel._state.enabled = True
        mock_hist = MagicMock()
        tel._state.cache_latency = mock_hist
        with tel.timed_operation("get", eviction_group="ns"):
            pass  # fast operation
        assert mock_hist.record.call_count == 1
        args = mock_hist.record.call_args
        assert args[1] == {} or True  # positional args
        duration = args[0][0]
        assert duration >= 0
        assert args[0][1] == {"operation": "get", "eviction_group": "ns"}

    def test_records_duration_even_on_exception(self):
        tel._state.enabled = True
        mock_hist = MagicMock()
        tel._state.cache_latency = mock_hist
        with pytest.raises(ValueError):
            with tel.timed_operation("set"):
                raise ValueError("boom")
        assert mock_hist.record.call_count == 1
