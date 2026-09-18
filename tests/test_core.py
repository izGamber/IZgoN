# Test suite for the delta engine and reference client.

from __future__ import annotations

import app
from izgon_client import apply_delta


def test_identical_states_return_no_change():
    state = {"temp": 21.5, "nested": {"ok": True}}
    result = app.DeltaSyncEngine().evaluate("sensor-01", state, state)
    assert result["status"] == "NO_CHANGE"
    assert result["delta"] is None
    assert result["bytes_sent"] == 0
    assert len(result["checksum"]) == 32


def test_nested_change_and_deletion_are_reconstructable():
    old = {"sensor": {"temp": 21.5, "hum": 60}, "battery": 98}
    new = {"sensor": {"temp": 22.0}, "battery": 98}
    result = app.DeltaSyncEngine().evaluate("sensor-01", old, new)
    assert result["status"] == "SYNC_REQUIRED"
    assert apply_delta(old, result["delta"]) == new
    assert result["bytes_sent"] <= result["bytes_full"]


def test_large_deletion_uses_full_state_when_delta_is_larger():
    old = {"a": 1, "b": 2, "c": 3, "d": 4}
    new = {"a": 1}
    result = app.DeltaSyncEngine().evaluate("sensor-01", old, new)
    assert result["status"] == "FULL_STATE"
    assert result["delta"] == new
    assert result["bytes_sent"] == result["bytes_full"]


def test_unicode_wire_size_is_utf8():
    state = {"greeting": "Ćirilica 日本語"}
    assert len(app._wire_bytes(state)) == len('{"greeting":"Ćirilica 日本語"}'.encode("utf-8"))


def test_reference_client_deletes_nested_keys():
    state = {"a": {"b": 1, "c": 2}, "keep": True}
    delta = {"a": {"c": {"__deleted__": True}}}
    assert apply_delta(state, delta) == {"a": {"b": 1}, "keep": True}


def test_invalid_license_is_safe():
    assert app.validate_license(None) is None
    assert app.validate_license("DPC-retired") is None
