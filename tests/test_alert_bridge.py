"""Regression coverage for grouped Grafana delivery; no external messages are sent."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def bridge(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "alert-mqtt-bridge" / "relay.py"
    spec = importlib.util.spec_from_file_location("alert_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "client", Mock())
    monkeypatch.setattr(module, "SMTP_HOST", "test.invalid")
    monkeypatch.setattr(module, "SMTP_TO", ["test@example.invalid"])
    monkeypatch.setattr(module, "TG_BOT_TOKEN", "test")
    monkeypatch.setattr(module, "TG_CHAT_IDS", ["test"])
    monkeypatch.setattr(module, "send_email", Mock())
    monkeypatch.setattr(module, "send_telegram", Mock())
    return module


def alert(name="DatasourceError", source="prometheus", status="firing", rule="agent"):
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


def test_four_query_errors_send_one_truthful_notification(bridge):
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


def test_distinct_sources_and_mixed_states_preserved_in_one_digest(bridge):
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


def test_real_alert_retains_critical_severity_in_mixed_group(bridge):
    bridge.publish_alerts({"alerts": [alert(), alert(name="BatteryLow")]})
    assert bridge.send_email.call_args.args[1] == "critical"
    assert "agent unreachable" in bridge.send_email.call_args.args[3]


def test_recovery_does_not_repeat_failed_query_as_current_fault(bridge):
    bridge.publish_alerts({"alerts": [alert(status="resolved")]})
    _, level, subject, body = bridge.send_email.call_args.args
    assert level == "info"
    assert subject.startswith("Resolved:")
    assert "connection refused" not in subject + body


def test_no_data_is_not_claimed_to_be_an_agent_failure(bridge):
    bridge.publish_alerts({"alerts": [alert(name="DatasourceNoData")]})
    assert "returned no data" in bridge.send_email.call_args.args[2]


def test_empty_payload_does_not_send_email_or_telegram(bridge):
    assert bridge.publish_alerts({"alerts": []}) == 0
    bridge.send_email.assert_not_called()
    bridge.send_telegram.assert_not_called()
