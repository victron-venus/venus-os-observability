"""Regression coverage for grouped Grafana delivery; no external messages are sent."""

import http.client
import importlib.util
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest


@pytest.fixture
def bridge(monkeypatch: pytest.MonkeyPatch) -> Any:
    path = Path(__file__).resolve().parents[1] / "alert-mqtt-bridge" / "relay.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("alert_bridge", path)
    assert spec is not None and spec.loader is not None
    module: Any = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "client", Mock())
    monkeypatch.setattr(module, "SMTP_HOST", "test.invalid")
    monkeypatch.setattr(module, "SMTP_TO", ["test@example.invalid"])
    monkeypatch.setattr(module, "TG_BOT_TOKEN", "test")
    monkeypatch.setattr(module, "TG_CHAT_IDS", ["test"])
    monkeypatch.setattr(module, "send_email", Mock())
    monkeypatch.setattr(module, "send_telegram", Mock())
    return module


def alert(
    name: str = "DatasourceError",
    source: str = "prometheus",
    status: str = "firing",
    rule: str = "agent",
) -> dict[str, Any]:
    return {
        "status": status,
        "fingerprint": rule,
        "labels": {
            "alertname": name,
            "datasource_uid": source,
            "rulename": rule,
            "severity": "critical",
        },
        "annotations": {
            "summary": "agent unreachable from Prometheus",
            "Error": "connect: connection refused",
        },
        "startsAt": "2026-09-24T00:00:00Z",
    }


def test_four_query_errors_send_one_truthful_notification(bridge: Any) -> None:
    alerts = [alert(rule=rule) for rule in ("agent", "inverter", "errors", "signals")]
    assert bridge.publish_alerts({"alerts": alerts}) == 4
    bridge.send_email.assert_called_once()
    bridge.send_telegram.assert_called_once()
    _, level, subject, body = bridge.send_email.call_args.args
    assert level == "warning"
    assert subject == "Data source prometheus query failed; 4 checks affected"
    assert "agent unreachable" not in subject + body
    assert "connection refused" in body
    for rule in ("agent", "inverter", "errors", "signals"):
        assert f"- {rule}" in body
    assert bridge.client.publish.call_count == 2  # One banner and retained snapshot.


def test_distinct_sources_and_mixed_states_preserved_in_one_digest(bridge: Any) -> None:
    alerts = [alert(), alert(source="loki"), alert(status="resolved", rule="recovered")]
    bridge.publish_alerts({"alerts": alerts})
    bridge.send_email.assert_called_once()
    _, level, subject, body = bridge.send_email.call_args.args
    assert level == "warning"
    assert subject == "2 active, 1 resolved monitoring notifications"
    assert "Data source prometheus query failed" in body
    assert "Data source loki query failed" in body
    assert "Resolved: Data source prometheus: query problem resolved" in body
    assert bridge.client.publish.call_count == 4


def test_real_alert_retains_critical_severity_in_mixed_group(bridge: Any) -> None:
    bridge.publish_alerts({"alerts": [alert(), alert(name="BatteryLow")]})
    assert bridge.send_email.call_args.args[1] == "critical"
    assert "agent unreachable" in bridge.send_email.call_args.args[3]


def test_recovery_does_not_repeat_failed_query_as_current_fault(bridge: Any) -> None:
    bridge.publish_alerts({"alerts": [alert(status="resolved")]})
    _, level, subject, body = bridge.send_email.call_args.args
    assert level == "info"
    assert subject.startswith("Resolved:")
    assert "connection refused" not in subject + body


def test_no_data_is_not_claimed_to_be_an_agent_failure(bridge: Any) -> None:
    bridge.publish_alerts({"alerts": [alert(name="DatasourceNoData")]})
    assert "returned no data" in bridge.send_email.call_args.args[2]


def test_empty_payload_does_not_send_email_or_telegram(bridge: Any) -> None:
    assert bridge.publish_alerts({"alerts": []}) == 0
    bridge.send_email.assert_not_called()
    bridge.send_telegram.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {"alerts": {}},
        {"alerts": None},
        {"alerts": [None]},
        {"alerts": [{"labels": []}]},
        {"alerts": [{"annotations": {"summary": 3}}]},
        {"alerts": [{"valueString": []}]},
        {"alerts": [{"status": "unexpected"}]},
        {"status": []},
        {"alerts": [{}] * 1001},
    ],
)
def test_rejects_malformed_batch_before_any_delivery(bridge: Any, payload: Any) -> None:
    with pytest.raises(ValueError):
        bridge.publish_alerts(payload)
    bridge.client.publish.assert_not_called()
    bridge.send_email.assert_not_called()
    bridge.send_telegram.assert_not_called()


def test_validates_late_batch_entries_before_first_delivery(bridge: Any) -> None:
    with pytest.raises(ValueError):
        bridge.publish_alerts({"alerts": [alert(), {"annotations": {"summary": False}}]})
    bridge.client.publish.assert_not_called()
    bridge.send_email.assert_not_called()


@pytest.fixture
def webhook_server(bridge: Any) -> Iterator[ThreadingHTTPServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ([], 400),
        ([("Content-Length", "-1")], 400),
        ([("Content-Length", "invalid")], 400),
        ([("Content-Length", "999999999999999999999999")], 413),
        ([("Content-Length", "1048577")], 413),
        ([("Content-Length", "2"), ("Content-Length", "2")], 400),
        ([("Content-Length", "2"), ("Transfer-Encoding", "chunked")], 400),
    ],
)
def test_http_rejects_unsafe_lengths_without_waiting_for_body(
    bridge: Any, webhook_server: ThreadingHTTPServer, headers: list[tuple[str, str]], expected: int
) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", webhook_server.server_port, timeout=2)
    try:
        connection.putrequest("POST", "/grafana")
        for name, value in headers:
            connection.putheader(name, value)
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == expected
        response.read()
    finally:
        connection.close()
    bridge.client.publish.assert_not_called()


@pytest.mark.parametrize("body", [b"[]", b'{"alerts":[null]}', b"\xff", b"[" * 2000])
def test_http_rejects_invalid_json(
    bridge: Any, webhook_server: ThreadingHTTPServer, body: bytes
) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", webhook_server.server_port, timeout=2)
    try:
        connection.request("POST", "/grafana", body)
        assert connection.getresponse().status == 400
    finally:
        connection.close()
    bridge.client.publish.assert_not_called()


def test_http_accepts_valid_empty_batch(bridge: Any, webhook_server: ThreadingHTTPServer) -> None:
    connection = http.client.HTTPConnection("127.0.0.1", webhook_server.server_port, timeout=2)
    try:
        connection.request("POST", "/grafana", b'{"alerts":[]}')
        assert connection.getresponse().status == 200
    finally:
        connection.close()
    bridge.client.publish.assert_called_once()
