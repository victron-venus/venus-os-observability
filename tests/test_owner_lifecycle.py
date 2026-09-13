"""Exercise asynchronous owner discovery, reconnects and bounded pending signals."""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from prometheus_client import CollectorRegistry, Counter, Gauge

from venus_observability import dbus_owners, metrics
from venus_observability.dbus_listener import DBusSignalListener
from venus_observability.dbus_owners import ServiceOwnerTracker

SERVICE = "com.victronenergy.battery.owner_test"


@dataclass
class Pending:
    """A real asynchronous call does not invoke its handler until the loop dispatches."""

    method: str
    args: tuple[str, ...]
    success: Callable[..., None]
    failure: Callable[..., None]
    handle: MagicMock = field(default_factory=MagicMock)


class FakeBus:
    """Keep D-Bus operations deferred and reject every synchronous proxy call."""

    def __init__(self) -> None:
        self.calls: list[Pending] = []
        self.receiver: Any = None
        self.receiver_options: dict[str, Any] = {}
        self.match = MagicMock()
        self.filters: list[Any] = []
        self.rules: list[str] = []

    def add_signal_receiver(self, receiver: Any, **kwargs: Any) -> MagicMock:
        """Retain the subscription so tests can dispatch daemon lifecycle events."""
        self.receiver = receiver
        self.receiver_options = kwargs
        return self.match

    def call_async(self, *parameters: Any, **options: Any) -> MagicMock:
        """Verify the low-level daemon call contract and defer its reply."""
        destination, path, interface, method, signature, args = parameters
        assert destination == interface == "org.freedesktop.DBus"
        assert path == "/org/freedesktop/DBus"
        assert signature == ("s" if args else "")
        assert 0 < options["timeout"] <= 5
        call = Pending(method, args, options["reply_handler"], options["error_handler"])
        self.calls.append(call)
        return call.handle

    def get_object(self, *args: Any, **kwargs: Any) -> None:
        """Fail immediately if production discovery attempts synchronous proxy work."""
        raise AssertionError("No synchronous proxy/introspection is allowed")

    def add_match_string(self, rule: str) -> None:
        """Record a native signal match."""
        self.rules.append(rule)

    def remove_match_string(self, rule: str) -> None:
        """Remove a previously installed match."""
        self.rules.remove(rule)

    def add_message_filter(self, callback: Any) -> None:
        """Record every registration so duplicate processing remains visible."""
        self.filters.append(callback)

    def remove_message_filter(self, callback: Any) -> None:
        """Remove a callback when the listener shuts down."""
        self.filters.remove(callback)

    def take(self, method: str, args: tuple[str, ...] = ()) -> Pending:
        """Take one outstanding reply for deliberate delivery or failure."""
        call = next(call for call in self.calls if call.method == method and call.args == args)
        self.calls.remove(call)
        return call

    def owner(self, name: str, previous: str, current: str) -> None:
        """Dispatch a daemon ownership notification through its actual callback."""
        assert self.receiver is not None
        self.receiver(name, previous, current)

    def items(self, owner: str, changed: dict[str, Any]) -> None:
        """Dispatch a complete ItemsChanged batch through installed filters."""
        message = MagicMock()
        message.get_member.return_value = "ItemsChanged"
        message.get_sender.return_value = owner
        message.get_args_list.return_value = [changed]
        for callback in self.filters:
            callback(self, message)


@pytest.fixture(name="scenario")
def make_scenario(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ServiceOwnerTracker, FakeBus, list[float], MagicMock, MagicMock]:
    """Use a monotonic fake clock and deferred D-Bus callbacks, never a real bus."""
    clock = [100.0]
    monkeypatch.setattr("venus_observability.dbus_owners.time.monotonic", lambda: clock[0])
    bus = FakeBus()
    delivered = MagicMock()
    invalidated = MagicMock()
    tracker = ServiceOwnerTracker(bus, delivered, invalidated)
    return tracker, bus, clock, delivered, invalidated


def test_snapshot_subscription_is_async_and_idempotent(scenario: Any) -> None:
    """Startup installs one authenticated daemon subscription and one async scan."""
    tracker, bus, _, _, _ = scenario
    tracker.start()
    tracker.start()
    assert len(bus.calls) == 1
    assert bus.receiver_options == {
        "signal_name": "NameOwnerChanged",
        "dbus_interface": "org.freedesktop.DBus",
        "bus_name": "org.freedesktop.DBus",
        "path": "/org/freedesktop/DBus",
    }


def test_startup_batches_replay_in_order_without_losing_text_or_invalid_values(
    scenario: Any,
) -> None:
    """Queue complete batches so later text cannot overwrite a pending numeric update."""
    tracker, bus, _, delivered, _ = scenario
    updates = [{"/Soc": {"Value": 50}}, {"/Soc": {"Text": "50%"}}, {"/Soc": {"Value": []}}]
    for update in updates:
        tracker.submit(":1.200", update, "correlation")
    delivered.assert_not_called()
    bus.take("ListNames").success([SERVICE])
    bus.take("GetNameOwner", (SERVICE,)).success(":1.200")
    assert [call.args for call in delivered.call_args_list] == [
        (SERVICE, update, "correlation") for update in updates
    ]
    assert not tracker.pending and tracker.pending_items == 0
    tracker.submit(":1.200", {"/Soc": {"Value": 51}}, None)
    assert delivered.call_args.args[1]["/Soc"]["Value"] == 51


def test_owner_change_during_list_names_takes_precedence(scenario: Any) -> None:
    """A lifecycle event during the initial name scan supplies the current identity."""
    tracker, bus, _, delivered, _ = scenario
    tracker.start()
    bus.owner(SERVICE, "", ":1.201")
    bus.take("ListNames").success([SERVICE])
    assert not bus.calls
    tracker.submit(":1.201", {"/Soc": {"Value": 52}}, None)
    delivered.assert_called_once_with(SERVICE, {"/Soc": {"Value": 52}}, None)


def test_late_snapshot_owner_cannot_restore_replaced_owner(scenario: Any) -> None:
    """An old GetNameOwner response must not resurrect a superseded process."""
    tracker, bus, _, delivered, invalidated = scenario
    tracker.start()
    bus.take("ListNames").success([SERVICE])
    stale = bus.take("GetNameOwner", (SERVICE,))
    bus.owner(SERVICE, "", ":1.202")
    bus.owner(SERVICE, ":1.202", ":1.203")
    stale.success(":1.202")
    assert tracker.owners == {":1.203": {SERVICE}}
    invalidated.assert_called_once_with(SERVICE)
    tracker.submit(":1.203", {"/Soc": {"Value": 53}}, None)
    delivered.assert_called_once()


def test_reconnects_keep_real_prometheus_series_and_owner_cache_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hundred real listener reconnects retain five stable Prometheus samples."""
    registry = CollectorRegistry()
    gauge = Gauge("test_soc", "SOC", ["serial"], registry=registry)
    counter = Counter(
        "test_signals", "Signals", ["service", "path", "interface"], registry=registry
    )
    monkeypatch.setattr(metrics, "battery_soc", gauge)
    monkeypatch.setattr(metrics, "dbus_signals_received", counter)
    bus = FakeBus()
    listener = DBusSignalListener(MagicMock(), bus=bus, tracer=MagicMock())
    listener.subscribe_global()
    bus.take("ListNames").success([SERVICE])
    bus.take("GetNameOwner", (SERVICE,)).success(":1.300")
    for index in range(100):
        old = f":1.{300 + index}"
        new = f":1.{301 + index}"
        bus.items(old, {"/Soc": {"Value": index}, "/CustomName": {"Value": "test"}})
        bus.owner(SERVICE, old, new)
        assert math.isnan(gauge.labels(serial="owner_test")._value.get())
        bus.items(new, {"/Soc": {"Value": index + 1}})
        assert len(listener._owners.owners) == len(listener._owners.services) == 1
    samples = [sample for family in registry.collect() for sample in family.samples]
    assert len(samples) == 5  # one gauge; two cumulative counters plus their created samples
    assert not any(value.startswith(":") for sample in samples for value in sample.labels.values())
    assert gauge.labels(serial="owner_test")._value.get() == 100
    assert not bus.calls  # reconnects use lifecycle signals, never full owner scans
    listener.stop()
    assert not bus.filters


def test_alias_removal_keeps_other_service_owner_and_identity(scenario: Any) -> None:
    """Releasing one name leaves another name on the same connection usable."""
    tracker, bus, _, delivered, invalidated = scenario
    tracker.start()
    bus.take("ListNames").success([])
    alias = SERVICE + "_other"
    bus.owner(SERVICE, "", ":1.401")
    bus.owner(alias, "", ":1.401")
    tracker.submit(":1.401", {"/Soc": 1}, None)
    assert delivered.call_args.args[0] == SERVICE
    bus.owner(SERVICE, ":1.401", "")
    assert tracker.owners == {":1.401": {alias}}
    invalidated.assert_called_once_with(SERVICE)
    tracker.submit(":1.401", {"/Soc": 2}, None)
    assert delivered.call_args.args[0] == alias


def test_pending_batch_ceiling_and_exact_expiry_boundary(
    scenario: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Unresolved traffic has finite storage, exact monotonic expiry and bounded logs."""
    tracker, _, clock, delivered, _ = scenario
    monkeypatch.setattr(dbus_owners, "MAX_PENDING_BATCHES", 3)
    dropped = MagicMock()
    monkeypatch.setattr(dbus_owners, "dbus_unresolved_signals_dropped", dropped)
    for index in range(8):
        tracker.submit(f":1.{index}", {"/Soc": index}, None)
    assert len(tracker.pending) == tracker.pending_items == 3
    assert dropped.labels.call_count == 5
    assert len(caplog.records) == 1
    clock[0] += 9.999
    tracker._tick()
    assert len(tracker.pending) == 3
    clock[0] += 0.001
    tracker._tick()
    assert not tracker.pending and tracker.pending_items == 0
    assert {call.kwargs["reason"] for call in dropped.labels.call_args_list} == {
        "overflow",
        "expired",
    }
    delivered.assert_not_called()


def test_pending_item_limit_does_not_block_known_signals(
    scenario: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Large unresolved batches cannot consume the budget or delay known services."""
    tracker, _, _, delivered, _ = scenario
    monkeypatch.setattr(dbus_owners, "MAX_PENDING_ITEMS", 3)
    tracker.submit(":1.900", {"/a": 1, "/b": 2}, None)
    tracker.submit(":1.901", {"/a": 1, "/b": 2}, None)
    tracker.submit(":1.902", {str(i): i for i in range(4)}, None)
    assert len(tracker.pending) == 1 and tracker.pending_items == 2
    tracker.submit(SERVICE, {"/Soc": 60}, None)
    delivered.assert_called_once_with(SERVICE, {"/Soc": 60}, None)


@pytest.mark.parametrize("failed_method", ["ListNames", "GetNameOwner"])
def test_failed_snapshot_retries_without_owner_event_before_queue_expiry(
    scenario: Any, failed_method: str
) -> None:
    """Recover pre-existing services after a temporary discovery error."""
    tracker, bus, clock, delivered, _ = scenario
    tracker.submit(":1.500", {"/Soc": 65}, None)
    listing = bus.take("ListNames")
    if failed_method == "ListNames":
        listing.failure(RuntimeError("temporary failure"))
    else:
        listing.success([SERVICE])
        bus.take("GetNameOwner", (SERVICE,)).failure(RuntimeError("temporary failure"))
    clock[0] += 4.9
    tracker._tick()
    assert not bus.calls
    clock[0] += 0.1
    tracker._tick()
    bus.take("ListNames").success([SERVICE])
    bus.take("GetNameOwner", (SERVICE,)).success(":1.500")
    delivered.assert_called_once_with(SERVICE, {"/Soc": 65}, None)


def test_snapshot_replaces_missing_cache_entries_without_lifecycle_event(scenario: Any) -> None:
    """A recovery snapshot removes names no longer present on the daemon."""
    tracker, bus, clock, _, invalidated = scenario
    tracker.start()
    bus.owner(SERVICE, "", ":1.600")
    bus.take("ListNames").success([SERVICE])
    clock[0] += 60
    tracker.submit(":1.601", {"/Soc": 20}, None)
    bus.take("ListNames").success([])
    assert not tracker.owners and not tracker.services
    invalidated.assert_called_once_with(SERVICE)


def test_stop_cancels_lookup_and_ignores_late_reply_and_lifecycle(scenario: Any) -> None:
    """Pending callbacks after shutdown cannot recreate ownership or measurements."""
    tracker, bus, _, delivered, _ = scenario
    tracker.submit(":1.700", {"/Soc": 30}, None)
    call = bus.take("ListNames")
    tracker.stop()
    call.handle.cancel.assert_called_once()
    bus.match.remove.assert_called_once()
    call.success([SERVICE])
    bus.owner(SERVICE, "", ":1.700")
    tracker.submit(SERVICE, {"/Soc": 40}, None)
    assert not tracker.pending and not tracker.owners and not tracker.services
    assert not bus.calls
    delivered.assert_not_called()


def test_multiple_path_subscriptions_install_only_one_message_filter() -> None:
    """Multiple match rules must not duplicate every metric update."""
    bus = FakeBus()
    received = MagicMock()
    listener = DBusSignalListener(received, bus=bus, tracer=MagicMock())
    listener.subscribe_service(SERVICE, ["/Soc", "/State", "/Dc/0/Power"])
    assert len(bus.filters) == 1
    bus.items(SERVICE, {"/Soc": 45})
    received.update_from_dbus.assert_called_once()
    listener.stop()
