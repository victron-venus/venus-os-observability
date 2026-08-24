"""
D-Bus signal listener for Victron devices with OpenTelemetry tracing.
"""

# Lazy annotations: Venus OS ships a stripped dbus-python without dbus.Message
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import Any

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from opentelemetry import trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

from .correlation import (
    CorrelationContext,
    extract_correlation_id_from_headers,
    get_correlation_id,
)
from .metrics import VictronMetrics, update_prometheus_from_dbus

# Initialize D-Bus main loop
DBusGMainLoop(set_as_default=True)

# Common D-Bus paths used across multiple service types
DC_VOLTAGE_PATH = "/Dc/0/Voltage"


class DBusSignalListener:
    """Async D-Bus signal listener for Victron services."""

    def __init__(
        self,
        metrics: VictronMetrics,
        bus: dbus.Bus | None = None,
        tracer: trace.Tracer | None = None,
    ):
        self.metrics = metrics
        self.bus = bus or dbus.SystemBus()
        self.tracer = tracer or trace.get_tracer(__name__)
        self.logger = logging.getLogger(__name__)
        self._main_loop: GLib.MainLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._subscriptions: set[tuple[str, str]] = set()
        self._match_rules: list[str] = []
        # Victron services emit from unique names (:1.x); map them to well-known names
        self._owner_cache: dict[str, str] = {}
        self._owner_cache_ts: float = 0.0

    def subscribe_global(self) -> None:
        """Subscribe to all Victron BusItem ItemsChanged signals.

        Victron services emit ItemsChanged from the root object path ``/``
        with a dict of absolute item paths, so one global match rule covers
        every service (per-path rules never match).
        """
        try:
            match_rule = (
                "type='signal',interface='com.victronenergy.BusItem',member='ItemsChanged',path='/'"
            )
            self.bus.add_match_string(match_rule)
            self.bus.add_message_filter(self._message_filter)
            self._match_rules.append(match_rule)
            self.logger.info("Subscribed to Victron ItemsChanged signals")
        except dbus.DBusException as e:
            self.logger.warning("Failed to subscribe to Victron signals: %s", e)

    def _resolve_service_name(self, unique_name: str | None) -> str | None:
        """Map a D-Bus unique name (:1.x) to its well-known Victron service name."""
        if not unique_name:
            return None
        if not unique_name.startswith(":"):
            return unique_name
        cached = self._owner_cache.get(unique_name)
        if cached:
            return cached
        # Refresh at most once per 60s on unknown senders
        now = time.monotonic()
        if now - self._owner_cache_ts > 60:
            self._refresh_owner_cache()
            self._owner_cache_ts = now
            return self._owner_cache.get(unique_name)
        return None

    def _refresh_owner_cache(self) -> None:
        """Build unique-name -> well-known-name cache for Victron services."""
        try:
            obj = self.bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
            iface = dbus.Interface(obj, "org.freedesktop.DBus")
            for name in iface.ListNames():
                if str(name).startswith("com.victronenergy."):
                    owner = iface.GetNameOwner(name)
                    self._owner_cache[str(owner)] = str(name)
        except dbus.DBusException as e:
            self.logger.warning("Owner cache refresh failed: %s", e)

    def subscribe(
        self,
        service: str,
        path: str,
        interface: str = "com.victronenergy.BusItem",
    ) -> None:
        """Subscribe to D-Bus signals for a service/path."""
        key = (service, path)
        if key in self._subscriptions:
            return

        try:
            match_rule = (
                f"type='signal',"
                f"sender='{service}',"
                f"path='{path}',"
                f"interface='{interface}',"
                f"member='ItemsChanged'"
            )
            self.bus.add_match_string(match_rule)
            self.bus.add_message_filter(self._message_filter)
            self._subscriptions.add(key)
            self._match_rules.append(match_rule)
            self.logger.debug("Subscribed to %s%s", service, path)
        except dbus.DBusException as e:
            self.logger.warning("Failed to subscribe to %s%s: %s", service, path, e)

    def subscribe_service(self, service: str, paths: list[str]) -> None:
        """Subscribe to multiple paths on a service."""
        for path in paths:
            self.subscribe(service, path)

    def _extract_headers_from_message(self, message: dbus.Message) -> dict[str, str]:
        """Extract correlation headers from D-Bus message."""
        headers: dict[str, str] = {}
        # Try to get correlation ID from message metadata if available
        # D-Bus doesn't have standard headers, but we can check for custom metadata
        with suppress(Exception):
            # Check for any custom properties or annotations
            # (This is a placeholder - D-Bus typically doesn't carry correlation IDs natively)
            pass
        return headers

    def _message_filter(self, bus: dbus.Bus, message: dbus.Message) -> None:
        """Handle incoming D-Bus signal messages."""
        if message.get_member() != "ItemsChanged":
            return

        try:
            raw_sender = str(message.get_sender() or "")
            args = message.get_args_list()

            if not args:
                return

            # ItemsChanged payload: dict of absolute item paths -> {Value, Text}
            changed = args[0] if isinstance(args[0], dict) else {}

            # Extract correlation ID from message if available
            headers = self._extract_headers_from_message(message)
            correlation_id = extract_correlation_id_from_headers(headers)

            service = self._resolve_service_name(raw_sender) or raw_sender or "unknown"

            with self._trace_signal(service, "/", dict(changed), correlation_id) as span:
                for key, value in changed.items():
                    full_path = str(key)
                    self._handle_value(service, full_path, value, span)

        except Exception as e:
            self.logger.error("Error processing D-Bus signal: %s", e)

    @contextmanager
    def _trace_signal(
        self, service: str, path: str, data: dict[str, Any], correlation_id: str | None = None
    ) -> Iterator[Span]:
        """Create trace span for D-Bus signal processing with correlation ID."""
        span_name = f"dbus.signal.{service.replace('.', '_')}{path.replace('/', '_')}"

        # Use correlation ID from context or provided
        corr_id = correlation_id or get_correlation_id()

        with self.tracer.start_as_current_span(
            span_name,
            kind=SpanKind.CONSUMER,
            attributes={
                "dbus.service": service,
                "dbus.path": path,
                "dbus.changed_keys": list(data.keys()),
                "correlation.id": corr_id or "",
            },
        ) as span:
            # Set correlation ID in context for downstream operations
            if corr_id:
                with CorrelationContext(corr_id):
                    try:
                        yield span
                    except Exception as e:
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                        raise
            else:
                try:
                    yield span
                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

    def _handle_value(self, service: str, path: str, value: Any, span: Span) -> None:
        """Process a single D-Bus value update."""
        start_time = time.perf_counter()

        # Add to span
        span.set_attribute(f"dbus.value.{path}", str(value))

        # Victron ItemsChanged values arrive as {Value: x, Text: "..."}
        raw = value
        if isinstance(raw, dict):
            raw = raw.get("Value", raw.get("Text"))

        # Update OpenTelemetry metrics
        self.metrics.update_from_dbus(service, path, raw)

        # Update Prometheus metrics
        update_prometheus_from_dbus(service, path, raw)

        # Record latency
        latency_ms = (time.perf_counter() - start_time) * 1000
        span.set_attribute("dbus.processing_latency_ms", latency_ms)

    def start(self) -> None:
        """Start the D-Bus event loop in a background thread."""
        self.logger.info("Starting D-Bus event loop")
        self._loop_thread = threading.Thread(
            target=self._run_loop, name="dbus-mainloop", daemon=True
        )
        self._loop_thread.start()

    def _run_loop(self) -> None:
        """Create and run the GLib main loop (must own its thread)."""
        self._main_loop = GLib.MainLoop()
        try:
            self._main_loop.run()
        except KeyboardInterrupt:
            self.logger.info("D-Bus event loop stopped")

    def run(self) -> None:
        """Run the D-Bus event loop (blocking)."""
        if not self._main_loop:
            self.start()
        assert self._main_loop is not None
        try:
            self._main_loop.run()
        except KeyboardInterrupt:
            self.logger.info("D-Bus event loop stopped")

    def stop(self) -> None:
        """Stop the D-Bus event loop."""
        self.logger.info("Stopping D-Bus event loop")
        if self._main_loop and self._main_loop.is_running():
            self._main_loop.quit()

        # Remove match rules
        for rule in self._match_rules:
            with suppress(dbus.DBusException):
                self.bus.remove_match_string(rule)
        self._match_rules.clear()
        self._subscriptions.clear()


class VictronServiceDiscovery:
    """Discover Victron services on D-Bus."""

    KNOWN_SERVICES = {
        "battery": [
            "/Soc",
            DC_VOLTAGE_PATH,
            "/Dc/0/Current",
            "/Dc/0/Power",
            "/Dc/0/Temperature",
            "/CustomName",
        ],
        "solarcharger": [
            "/Yield/Power",
            DC_VOLTAGE_PATH,
            "/Dc/0/Current",
            "/Dc/0/Power",
        ],
        "vebus": [
            "/State",
            "/Ac/Out/L1/P",
            "/Ac/Grid/L1/P",
            DC_VOLTAGE_PATH,
        ],
        "grid": [
            "/Ac/Power",
            "/Ac/L1/Power",
        ],
    }

    def __init__(self, bus: dbus.Bus | None = None):
        self.bus = bus or dbus.SystemBus()
        self.logger = logging.getLogger(__name__)

    def find_victron_services(self) -> dict[str, list[str]]:
        """Find all Victron services and their object paths."""
        services = {}

        try:
            obj = self.bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus")
            iface = dbus.Interface(obj, "org.freedesktop.DBus")
            names = iface.ListNames()

            for name in names:
                if name.startswith("com.victronenergy."):
                    paths = self._introspect_service(name)
                    if paths:
                        services[name] = paths

        except dbus.DBusException as e:
            self.logger.error("Service discovery failed: %s", e)

        return services

    def _introspect_service(self, service: str) -> list[str]:
        """Introspect a service to find object paths."""
        paths = []
        try:
            obj = self.bus.get_object(service, "/")
            xml = obj.Introspect(dbus_interface="org.freedesktop.DBus.Introspectable")
            import xml.etree.ElementTree as ET

            root = ET.fromstring(xml)
            for node in root.findall(".//node"):
                node_name = node.get("name")
                if node_name:
                    paths.append(f"/{node_name}")
        except dbus.DBusException:
            pass
        return paths

    def get_known_paths(self, service: str) -> list[str]:
        """Get known paths for a service type."""
        for service_type, paths in self.KNOWN_SERVICES.items():
            if service_type in service.lower():
                return paths
        return []


async def create_listener(metrics: VictronMetrics) -> DBusSignalListener:
    """Create and configure a D-Bus listener for known Victron services."""
    listener = DBusSignalListener(metrics)
    discovery = VictronServiceDiscovery(listener.bus)

    # Discover and subscribe to services
    services = discovery.find_victron_services()
    for service, paths in services.items():
        known_paths = discovery.get_known_paths(service)
        if known_paths:
            listener.subscribe_service(service, known_paths)
        else:
            # Subscribe to all discovered paths
            for path in paths:
                listener.subscribe(service, path)

    return listener
