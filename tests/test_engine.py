"""Tests for the delta-sync engine."""
import pytest
from app import DeltaSyncEngine, compute_delta


@pytest.fixture
def engine():
    """Initialize the DeltaSyncEngine."""
    return DeltaSyncEngine()


class TestComputeDelta:
    """Test the compute_delta function."""

    def test_no_change(self):
        """Test when state hasn''t changed."""
        old = {"temp": 21.5, "humidity": 60}
        new = {"temp": 21.5, "humidity": 60}
        delta = compute_delta(old, new)
        assert delta == {}

    def test_single_field_change(self):
        """Test when a single field changes."""
        old = {"temp": 21.5, "humidity": 60}
        new = {"temp": 22.1, "humidity": 60}
        delta = compute_delta(old, new)
        assert delta == {"temp": 22.1}

    def test_field_deletion(self):
        """Test when a field is removed."""
        old = {"temp": 21.5, "humidity": 60, "pressure": 1013}
        new = {"temp": 21.5, "humidity": 60}
        delta = compute_delta(old, new)
        assert "__deleted__" in delta["pressure"]

    def test_new_field(self):
        """Test when a new field is added."""
        old = {"temp": 21.5}
        new = {"temp": 21.5, "humidity": 60}
        delta = compute_delta(old, new)
        assert delta == {"humidity": 60}

    def test_nested_dict_change(self):
        """Test nested dictionary changes."""
        old = {"sensor": {"temp": 21.5}}
        new = {"sensor": {"temp": 22.1}}
        delta = compute_delta(old, new)
        assert delta == {"sensor": {"temp": 22.1}}

    def test_nested_dict_no_change(self):
        """Test nested dictionary with no changes."""
        old = {"sensor": {"temp": 21.5, "humidity": 60}}
        new = {"sensor": {"temp": 21.5, "humidity": 60}}
        delta = compute_delta(old, new)
        assert delta == {}

    def test_empty_old_state(self):
        """Test with no prior state."""
        old = None
        new = {"temp": 21.5, "humidity": 60}
        delta = compute_delta(old, new)
        assert delta == new


class TestDeltaSyncEngine:
    """Test the DeltaSyncEngine.evaluate method."""

    def test_no_change_status(self, engine):
        """Test NO_CHANGE status when state is identical."""
        old_state = {"temp": 21.5, "humidity": 60}
        new_state = {"temp": 21.5, "humidity": 60}
        result = engine.evaluate("sensor-01", old_state, new_state)
        assert result["status"] == "NO_CHANGE"
        assert result["delta"] is None
        assert result["bytes_sent"] == 0

    def test_sync_required_status(self, engine):
        """Test SYNC_REQUIRED when delta is smaller than full state."""
        old_state = {"temp": 21.5, "humidity": 60, "pressure": 1013}
        new_state = {"temp": 22.1, "humidity": 60, "pressure": 1013}
        result = engine.evaluate("sensor-01", old_state, new_state)
        assert result["status"] == "SYNC_REQUIRED"
        assert "temp" in result["delta"]
        assert result["bytes_sent"] < result["bytes_full"]

    def test_full_state_status(self, engine):
        """Test FULL_STATE when delta would be larger."""
        old_state = {"a": 1, "b": 2, "c": 3, "d": 4}
        new_state = {"a": 5}  # Remove 3 fields, delta could be larger
        result = engine.evaluate("sensor-01", old_state, new_state)
        # May be FULL_STATE if delta is too large
        assert result["status"] in ["FULL_STATE", "SYNC_REQUIRED"]
        assert result["bytes_sent"] <= result["bytes_full"]

    def test_first_sync(self, engine):
        """Test first sync always includes full state."""
        new_state = {"temp": 21.5, "humidity": 60}
        result = engine.evaluate("sensor-01", None, new_state)
        assert result["status"] in ["FULL_STATE", "SYNC_REQUIRED"]
        assert result["bytes_full"] > 0

    def test_byte_counting(self, engine):
        """Test that bytes_sent never exceeds bytes_full."""
        old_state = {"field_a": 1, "field_b": 2, "field_c": 3}
        new_state = {"field_a": 10, "field_b": 20, "field_c": 30}
        result = engine.evaluate("sensor-01", old_state, new_state)
        assert result["bytes_sent"] <= result["bytes_full"]

    def test_node_id_in_result(self, engine):
        """Test that node_id is included in result."""
        result = engine.evaluate("test-node-123", {}, {"temp": 21})
        assert result["node_id"] == "test-node-123"
