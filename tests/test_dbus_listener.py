"""
Tests for D-Bus listener and service discovery.
"""

# pylint: disable=import-error,wrong-import-position,protected-access
# pylint: disable=redefined-outer-name,unused-variable,unused-argument

import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "src")
from venus_observability.dbus_listener import (
    DBusSignalListener,
    VictronServiceDiscovery,
)
from venus_observability.metrics import VictronMetrics


@pytest.fixture
def mock_metrics() -> MagicMock:
    """Create a mock VictronMetrics."""
    return MagicMock(spec=VictronMetrics)


@pytest.fixture
def mock_tracer() -> MagicMock:
    """Create a mock tracer."""
    return MagicMock()


@pytest.fixture
def mock_dbus_bus() -> MagicMock:
    """Create a mock D-Bus bus."""
    return MagicMock()


class TestDBusSignalListener:
    """Tests for DBusSignalListener."""

    def test_init(
        self, mock_metrics: MagicMock, mock_tracer: MagicMock, mock_dbus_bus: MagicMock
    ) -> None:
        """Test listener initialization."""
        listener = DBusSignalListener(metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer)
        assert listener.metrics is mock_metrics
        assert listener.bus is mock_dbus_bus
        assert listener.tracer is mock_tracer
        assert listener._subscriptions == set()
        assert listener._match_rules == []

    def test_subscribe_creates_match_rule(
        self, mock_metrics: MagicMock, mock_tracer: MagicMock, mock_dbus_bus: MagicMock
    ) -> None:
        """Test subscribe creates correct match rule."""
        listener = DBusSignalListener(metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer)
        listener.subscribe("com.victronenergy.battery.ttyO1", "/Soc")

        # Verify match string was added
        mock_dbus_bus.add_match_string.assert_called_once()
        call_args = mock_dbus_bus.add_match_string.call_args[0][0]
        assert "type='signal'" in call_args
        assert "sender='com.victronenergy.battery.ttyO1'" in call_args
        assert "path='/Soc'" in call_args
        assert "interface='com.victronenergy.BusItem'" in call_args
        assert "member='ItemsChanged'" in call_args

        # Verify subscription tracked
        assert ("com.victronenergy.battery.ttyO1", "/Soc") in listener._subscriptions
        assert len(listener._match_rules) == 1

    def test_subscribe_duplicate_ignored(
        self, mock_metrics: MagicMock, mock_tracer: MagicMock, mock_dbus_bus: MagicMock
    ) -> None:
        """Test duplicate subscriptions are ignored."""
        listener = DBusSignalListener(metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer)
        listener.subscribe("com.victronenergy.battery.ttyO1", "/Soc")
        listener.subscribe("com.victronenergy.battery.ttyO1", "/Soc")

        # Should only call add_match_string once
        assert mock_dbus_bus.add_match_string.call_count == 1

    def test_subscribe_service(
        self, mock_metrics: MagicMock, mock_tracer: MagicMock, mock_dbus_bus: MagicMock
    ) -> None:
        """Test subscribing to multiple paths on a service."""
        listener = DBusSignalListener(metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer)
        paths = ["/Soc", "/Dc/0/Voltage", "/Dc/0/Current"]
        listener.subscribe_service("com.victronenergy.battery.ttyO1", paths)

        assert mock_dbus_bus.add_match_string.call_count == 3
        for path in paths:
            assert ("com.victronenergy.battery.ttyO1", path) in listener._subscriptions

    def test_subscribe_handles_dbus_exception(
        self, mock_metrics: MagicMock, mock_tracer: MagicMock, mock_dbus_bus: MagicMock
    ) -> None:
        """Test subscribe handles D-Bus exceptions gracefully."""
        import dbus

        mock_dbus_bus.add_match_string.side_effect = dbus.DBusException("Failed")

        listener = DBusSignalListener(metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer)
        # Should not raise
        listener.subscribe("com.victronenergy.battery.ttyO1", "/Soc")
        # Verify exception was handled (no crash)
        mock_dbus_bus.add_match_string.assert_called_once()

    @patch("venus_observability.dbus_listener.trace")
    def test_message_filter_items_changed(
        self,
        mock_trace: MagicMock,
        mock_metrics: MagicMock,
        mock_tracer: MagicMock,
        mock_dbus_bus: MagicMock,
    ) -> None:
        """Test message filter processes ItemsChanged signals."""
        listener = DBusSignalListener(metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer)

        # Create mock message
        mock_message = MagicMock()
        mock_message.get_member.return_value = "ItemsChanged"
        mock_message.get_sender.return_value = "com.victronenergy.battery.ttyO1"
        mock_message.get_path.return_value = "/Soc"
        mock_message.get_args_list.return_value = [
            {"Value": 85.5},  # changed
            [],  # removed
        ]

        # Mock tracer span context manager
        mock_span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_tracer.start_as_current_span.return_value.__exit__ = MagicMock(return_value=False)

        # Call message filter
        listener._message_filter(mock_dbus_bus, mock_message)

        # Verify metrics update was called
        mock_metrics.update_from_dbus.assert_called_once()
        args, _ = mock_metrics.update_from_dbus.call_args
        assert args[0] == "com.victronenergy.battery.ttyO1"
        # Path should be /Soc/Value (path + key)
        assert args[1] == "/Soc/Value"
        assert args[2] == 85.5

    @patch("venus_observability.dbus_listener.trace")
    def test_message_filter_ignores_non_items_changed(
        self,
        mock_trace: MagicMock,
        mock_metrics: MagicMock,
        mock_tracer: MagicMock,
        mock_dbus_bus: MagicMock,
    ) -> None:
        """Test message filter ignores non-ItemsChanged messages."""
        listener = DBusSignalListener(metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer)

        mock_message = MagicMock()
        mock_message.get_member.return_value = "SomeOtherSignal"

        listener._message_filter(mock_dbus_bus, mock_message)

        mock_metrics.update_from_dbus.assert_not_called()

    def test_message_filter_ignores_empty_args(
        self, mock_metrics: MagicMock, mock_tracer: MagicMock, mock_dbus_bus: MagicMock
    ) -> None:
        """Test message filter ignores messages with no args."""
        listener = DBusSignalListener(metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer)

        mock_message = MagicMock()
        mock_message.get_member.return_value = "ItemsChanged"
        mock_message.get_args_list.return_value = []

        listener._message_filter(mock_dbus_bus, mock_message)

        mock_metrics.update_from_dbus.assert_not_called()

    def test_start_creates_main_loop(
        self, mock_metrics: MagicMock, mock_tracer: MagicMock, mock_dbus_bus: MagicMock
    ) -> None:
        """Test start creates GLib main loop."""
        with patch("venus_observability.dbus_listener.GLib") as mock_glib:
            mock_main_loop = MagicMock()
            mock_glib.MainLoop.return_value = mock_main_loop

            listener = DBusSignalListener(
                metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer
            )
            listener.start()

            assert listener._main_loop is mock_main_loop
            mock_glib.MainLoop.assert_called_once()

    def test_stop_quits_main_loop(
        self, mock_metrics: MagicMock, mock_tracer: MagicMock, mock_dbus_bus: MagicMock
    ) -> None:
        """Test stop quits main loop and removes matches."""
        with patch("venus_observability.dbus_listener.GLib"):
            listener = DBusSignalListener(
                metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer
            )
            listener._main_loop = MagicMock()
            listener._main_loop.is_running.return_value = True
            listener._match_rules = ["rule1", "rule2"]

            listener.stop()

            listener._main_loop.quit.assert_called_once()
            assert mock_dbus_bus.remove_match_string.call_count == 2
            assert listener._match_rules == []
            assert listener._subscriptions == set()

    def test_stop_handles_not_running(
        self, mock_metrics: MagicMock, mock_tracer: MagicMock, mock_dbus_bus: MagicMock
    ) -> None:
        """Test stop handles non-running main loop."""
        listener = DBusSignalListener(metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer)
        listener._main_loop = MagicMock()
        listener._main_loop.is_running.return_value = False

        # Should not raise
        listener.stop()
        listener._main_loop.quit.assert_not_called()


class TestVictronServiceDiscovery:
    """Tests for VictronServiceDiscovery."""

    def test_init(self, mock_dbus_bus: MagicMock) -> None:
        """Test discovery initialization."""
        discovery = VictronServiceDiscovery(bus=mock_dbus_bus)
        assert discovery.bus is mock_dbus_bus

    def test_find_victron_services(self, mock_dbus_bus: MagicMock) -> None:
        """Test finding Victron services."""
        discovery = VictronServiceDiscovery(bus=mock_dbus_bus)

        # Mock DBus introspection
        mock_obj = MagicMock()
        mock_iface = MagicMock()
        mock_iface.ListNames.return_value = [
            "com.victronenergy.battery.ttyO1",
            "com.victronenergy.solarcharger.ttyO0",
            "org.freedesktop.DBus",
        ]

        # Need to mock dbus.Interface
        with patch("venus_observability.dbus_listener.dbus.Interface", return_value=mock_iface):
            mock_service_obj = MagicMock()
            mock_service_obj.Introspect.side_effect = [
                '<node><node name="Soc"/><node name="Dc"/></node>',  # battery
                '<node><node name="Yield"/><node name="Dc"/></node>',  # solarcharger
            ]
            mock_dbus_bus.get_object.side_effect = [
                mock_obj,  # for org.freedesktop.DBus
                mock_service_obj,  # for battery
                mock_service_obj,  # for solarcharger
            ]

            services = discovery.find_victron_services()

        assert "com.victronenergy.battery.ttyO1" in services
        assert "com.victronenergy.solarcharger.ttyO0" in services
        assert "org.freedesktop.DBus" not in services
        assert "/Soc" in services["com.victronenergy.battery.ttyO1"]
        assert "/Dc" in services["com.victronenergy.battery.ttyO1"]

    def test_find_victron_services_handles_exception(self, mock_dbus_bus: MagicMock) -> None:
        """Test find_victron_services handles DBus exceptions."""
        import dbus

        discovery = VictronServiceDiscovery(bus=mock_dbus_bus)
        mock_dbus_bus.get_object.side_effect = dbus.DBusException("Failed")

        services = discovery.find_victron_services()
        assert services == {}

    def test_introspect_service(self, mock_dbus_bus: MagicMock) -> None:
        """Test service introspection."""
        discovery = VictronServiceDiscovery(bus=mock_dbus_bus)
        mock_obj = MagicMock()
        mock_obj.Introspect.return_value = '<node><node name="Soc"/><node name="Dc"/></node>'
        mock_dbus_bus.get_object.return_value = mock_obj

        paths = discovery._introspect_service("com.victronenergy.battery.ttyO1")

        assert "/Soc" in paths
        assert "/Dc" in paths

    def test_get_known_paths(self, mock_dbus_bus: MagicMock) -> None:
        """Test getting known paths for service types."""
        discovery = VictronServiceDiscovery(bus=mock_dbus_bus)

        battery_paths = discovery.get_known_paths("com.victronenergy.battery.ttyO1")
        assert "/Soc" in battery_paths
        assert "/Dc/0/Voltage" in battery_paths
        assert "/Dc/0/Current" in battery_paths
        assert "/Dc/0/Power" in battery_paths

        solarcharger_paths = discovery.get_known_paths("com.victronenergy.solarcharger.ttyO0")
        assert "/Yield/Power" in solarcharger_paths
        assert "/Dc/0/Voltage" in solarcharger_paths
        assert "/Dc/0/Current" in solarcharger_paths
        assert "/Dc/0/Power" in solarcharger_paths

        vebus_paths = discovery.get_known_paths("com.victronenergy.vebus.ttyO2")
        assert "/State" in vebus_paths
        assert "/Ac/Out/L1/P" in vebus_paths
        assert "/Ac/Grid/L1/P" in vebus_paths

        unknown_paths = discovery.get_known_paths("com.unknown.service")
        assert unknown_paths == []


class TestExtractHeadersFromMessage:
    """Tests for extracting headers from D-Bus message."""

    def test_extract_headers_returns_empty_dict(
        self, mock_metrics: MagicMock, mock_tracer: MagicMock, mock_dbus_bus: MagicMock
    ) -> None:
        """Test _extract_headers_from_message returns empty dict."""
        listener = DBusSignalListener(metrics=mock_metrics, bus=mock_dbus_bus, tracer=mock_tracer)
        mock_message = MagicMock()
        headers = listener._extract_headers_from_message(mock_message)
        assert headers == {}


@patch("venus_observability.dbus_listener.DBusSignalListener")
@patch("venus_observability.dbus_listener.VictronServiceDiscovery")
async def test_create_listener(
    mock_discovery_class: MagicMock, mock_listener_class: MagicMock, mock_metrics: MagicMock
) -> None:
    """Test create_listener async function."""
    from venus_observability.dbus_listener import create_listener

    mock_listener = MagicMock()
    mock_listener.bus = MagicMock()
    mock_listener_class.return_value = mock_listener

    mock_discovery = MagicMock()
    mock_discovery.find_victron_services.return_value = {
        "com.victronenergy.battery.ttyO1": ["/Soc", "/Dc/0/Voltage"]
    }
    mock_discovery.get_known_paths.return_value = ["/Soc"]
    mock_discovery_class.return_value = mock_discovery

    result = await create_listener(mock_metrics)

    assert result is mock_listener
    mock_listener_class.assert_called_once_with(mock_metrics)
    mock_discovery_class.assert_called_once_with(mock_listener.bus)
    mock_listener.subscribe_service.assert_called_once()
