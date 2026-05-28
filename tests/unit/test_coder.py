"""Tests for JsonCoder encode/decode."""

import pytest

from redis_fastapi.types import Coder, JsonCoder


@pytest.mark.unit
class TestJsonCoder:
    def test_round_trip_dict(self) -> None:
        data = {"hello": "world", "n": 42}
        encoded = JsonCoder.encode(data)
        assert isinstance(encoded, str)
        decoded = JsonCoder.decode(encoded)
        assert decoded == data

    def test_round_trip_list(self) -> None:
        data = [1, 2, "three"]
        assert JsonCoder.decode(JsonCoder.encode(data)) == data

    def test_round_trip_string(self) -> None:
        data = "plain string"
        assert JsonCoder.decode(JsonCoder.encode(data)) == data

    def test_round_trip_number(self) -> None:
        assert JsonCoder.decode(JsonCoder.encode(3.14)) == 3.14

    def test_round_trip_none(self) -> None:
        assert JsonCoder.decode(JsonCoder.encode(None)) is None

    def test_non_serialisable_raises(self) -> None:
        """encode raises TypeError on non-JSON-serializable types."""
        import datetime

        dt = datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc)
        with pytest.raises(TypeError):
            JsonCoder.encode(dt)

    def test_satisfies_coder_protocol(self) -> None:
        assert isinstance(JsonCoder, type)
        # Runtime check via Protocol
        assert issubclass(JsonCoder, Coder)


@pytest.mark.unit
class TestCustomCoder:
    def test_custom_coder_protocol(self) -> None:
        class ReverseCoder:
            @classmethod
            def encode(cls, value):
                return str(value)[::-1]

            @classmethod
            def decode(cls, value):
                return value[::-1]

        assert isinstance(ReverseCoder(), Coder)
        assert ReverseCoder.decode(ReverseCoder.encode("hello")) == "hello"
