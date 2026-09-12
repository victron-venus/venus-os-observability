"""Unavailable items must not discard the rest of a Venus ItemsChanged batch."""

import math
from unittest.mock import MagicMock

import pytest

from venus_observability.dbus_listener import DBusSignalListener
from venus_observability.metrics import VictronMetrics, grid_power, inverter_state


def test_invalid_array_does_not_discard_later_phase(caplog: pytest.LogCaptureFixture) -> None:
    """Mirror the empty-array invalidation observed when the grid meter vanished."""
    metrics = VictronMetrics(MagicMock())
    listener = DBusSignalListener(metrics, bus=MagicMock(), tracer=MagicMock())
    message = MagicMock()
    message.get_member.return_value = "ItemsChanged"
    message.get_sender.return_value = "com.victronenergy.system.batch_test"
    grid_power.labels(serial="batch_test", phase="l1").set(125)
    message.get_args_list.return_value = [
        {
            "/Ac/Grid/L1/Power": {"Value": [], "Text": "---"},
            "/State": {"Value": []},
            "/Ac/Grid/L2/Power": {"Value": 42},
        }
    ]
    listener._message_filter(listener.bus, message)
    assert math.isnan(grid_power.labels(serial="batch_test", phase="l1")._value.get())
    assert math.isnan(inverter_state.labels(serial="batch_test")._value.get())
    assert grid_power.labels(serial="batch_test", phase="l2")._value.get() == 42
    assert not caplog.records


def test_text_only_update_keeps_numeric_reading() -> None:
    """Display text is not a numeric measurement or an explicit invalidation."""
    metrics = MagicMock(spec=VictronMetrics)
    listener = DBusSignalListener(metrics, bus=MagicMock(), tracer=MagicMock())
    grid_power.labels(serial="text_test", phase="l1").set(125)
    listener._handle_value(
        "com.victronenergy.system.text_test", "/Ac/Grid/L1/Power", {"Text": "125 W"}, MagicMock()
    )
    metrics.update_from_dbus.assert_not_called()
    assert grid_power.labels(serial="text_test", phase="l1")._value.get() == 125
