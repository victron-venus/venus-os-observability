#!/usr/bin/env python3
"""Exercise the deployed relay over HTTP with all outgoing channels mocked.

Run inside the bridge container: python3 - < smoke_test.py
Uses a separate module and ephemeral loopback port; never posts to the live
listener or sends email, Telegram or MQTT traffic.
"""

import importlib.util
import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("bridge_smoke", "/app/relay.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
bridge.client = Mock()
bridge.send_email = Mock()
bridge.send_telegram = Mock()
bridge.SMTP_HOST = "mock.invalid"
bridge.SMTP_TO = ["mock@example.invalid"]
bridge.TG_BOT_TOKEN = "mock"
bridge.TG_CHAT_IDS = ["mock"]
server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "DatasourceError",
                    "datasource_uid": "prometheus",
                    "rulename": rule,
                    "severity": "critical",
                },
                "annotations": {
                    "summary": "MISLEADING agent failure",
                    "Error": "connection refused",
                },
            }
            for rule in ("agent", "inverter", "signals", "errors")
        ],
    }
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        connection.request(
            "POST", "/grafana", json.dumps(payload), {"Content-Type": "application/json"}
        )
        response = connection.getresponse()
        assert response.status == 200
        response.read()
    finally:
        connection.close()
    bridge.send_email.assert_called_once()
    bridge.send_telegram.assert_called_once()
    _, level, subject, body = bridge.send_email.call_args.args
    assert level == "warning"
    assert "4 checks affected" in subject
    assert "MISLEADING" not in subject + body
    assert "connection refused" in body
    assert bridge.client.publish.call_count == 2
    bridge.log.info(
        "PASS: deployed relay HTTP request, 4 errors -> 1 email/Telegram digest and 1 MQTT banner"
    )
    bridge.log.info("PASS: all outbound channels mocked; no production messages sent")
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
