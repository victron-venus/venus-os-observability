"""Grafana alert webhook -> MQTT notification bridge.

Receives Grafana Alerting webhooks and republishes each alert as a
notification on the shared ``inverter/notifications`` topic, so all
dashboards (desktop, py, go) show the banner without frontend changes.

Endpoints:
    POST /grafana  - Grafana webhook payload (JSON)
    GET  /health   - liveness + MQTT connection state
"""

import json
import logging
import os
import smtplib
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import paho.mqtt.client as mqtt

MQTT_HOST = os.environ.get("MQTT_HOST", "192.168.160.150")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
NOTIFY_TOPIC = os.environ.get("ALERT_TOPIC", "inverter/notifications")
STATE_TOPIC = os.environ.get("STATE_TOPIC", "venus/alerts")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8095"))

# Optional email fan-out via external SMTP relay (e.g. Brevo :587).
# Empty SMTP_HOST or SMTP_TO disables the channel.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
SMTP_TO = [a.strip() for a in os.environ.get("SMTP_TO", "").split(",") if a.strip()]

# Optional Telegram fan-out via the Bot API.
# Empty TG_BOT_TOKEN or TG_CHAT_ID disables the channel.
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
TG_CHAT_IDS = [c.strip() for c in os.environ.get("TG_CHAT_ID", "").split(",") if c.strip()]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("alert-mqtt-bridge")

client = mqtt.Client(client_id="grafana-alert-bridge", clean_session=True)
client.reconnect_delay_set(1, 60)

_connect_event = threading.Event()


def _on_connect(_c: Any, _u: Any, _f: Any, rc: int, *_props: Any) -> None:
    if rc == 0:
        log.info("Connected to MQTT %s:%s", MQTT_HOST, MQTT_PORT)
        _connect_event.set()
    else:
        log.warning("MQTT connect failed rc=%s", rc)


def _on_disconnect(_c: Any, _u: Any, rc: int) -> None:
    _connect_event.clear()
    log.warning("MQTT disconnected rc=%s (auto-reconnect)", rc)


client.on_connect = _on_connect
client.on_disconnect = _on_disconnect


def send_email(name: str, level: str, summary: str, value: str) -> None:
    """Forward one alert as email via the SMTP relay. Best-effort."""
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = ", ".join(SMTP_TO)
    msg["Subject"] = f"[Venus] {level.upper()}: {summary}"
    msg.set_content((f"{summary}\n\n{value}" if value else summary) + "\n")
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        log.info("Email sent: %s", name)
    except (OSError, smtplib.SMTPException) as e:
        log.error("Email send failed for %s: %s", name, e)


def compact_value(value: str) -> str:
    """Shrink Grafana's verbose valueString to 'A=0 B=1'; pass through anything else.

    Uses linear string scans (no backtracking regex) so pathological webhook
    payloads cannot trigger ReDoS. Input is capped for defense in depth.
    """
    raw = value or ""
    if len(raw) > 8192:
        raw = raw[:8192]
    pairs: list[tuple[str, str]] = []
    pos = 0
    while True:
        i = raw.find("var='", pos)
        if i < 0:
            break
        j = raw.find("'", i + 5)
        if j < 0:
            break
        var = raw[i + 5 : j]
        k = raw.find("value=", j)
        if k < 0:
            break
        start = k + 6
        end = start
        while end < len(raw) and raw[end] not in " \t\n\r]":
            end += 1
        num = raw[start:end]
        if var and num:
            pairs.append((var, num))
        pos = end if end > pos else j + 1
    if not pairs:
        return raw.strip()
    return " ".join(f"{var}={num}" for var, num in pairs)


def _is_var_eq_token(part: str) -> bool:
    """True for compact tokens like A=0 (no regex — CodeQL-safe)."""
    eq = part.find("=")
    if eq <= 0 or eq == len(part) - 1:
        return False
    key, val = part[:eq], part[eq + 1 :]
    if not (key[0].isalpha() or key[0] == "_"):
        return False
    if not all(c.isalnum() or c == "_" for c in key):
        return False
    return bool(val) and not any(c.isspace() for c in val)


def human_alert_value(value: str) -> str:
    """Drop opaque Grafana query-var dumps like 'A=0 B=1' for email/Telegram."""
    v = (value or "").strip()
    if not v:
        return ""
    parts = v.split()
    if parts and all(_is_var_eq_token(part) for part in parts):
        return ""
    return v


def send_telegram(name: str, level: str, summary: str, value: str) -> None:
    """Forward one alert as Telegram messages via the Bot API. Best-effort."""
    icon = "🔴" if level == "critical" else ("🟡" if level == "warning" else "🟢")
    text = f"{icon} [Venus] {level.upper()}: {summary}"
    if value:
        text += f"\n{value}"
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    for chat_id in TG_CHAT_IDS:
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=15) as resp:
                resp.read()
            log.info("Telegram sent to %s: %s", chat_id, name)
        except (OSError, urllib.error.URLError) as e:
            log.error("Telegram send failed for %s -> %s: %s", name, chat_id, e)


def _banner_fields(
    status: str, severity: str, summary: str, human: str
) -> tuple[str, str, str, str]:
    """Map Grafana alert to email/TG level + desktop MQTT banner fields."""
    if status == "resolved":
        return "info", "info", summary, "Grafana RESOLVED"
    channel_level = "critical" if severity == "critical" else "warning"
    mqtt_level = "alarm" if severity == "critical" else "warning"
    return channel_level, mqtt_level, summary, human


def coalesce_datasource_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe a failed data source once, without claiming its checks failed."""
    notifications = []
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for alert in alerts:
        labels = alert.get("labels", {})
        name = labels.get("alertname", "unknown")
        if name not in {"DatasourceError", "DatasourceNoData"}:
            notifications.append(alert)
            continue
        key = (name, labels.get("datasource_uid", "unknown"), alert.get("status", "firing"))
        groups.setdefault(key, []).append(alert)
    for (name, source, status), affected in groups.items():
        checks = sorted(
            {
                a.get("labels", {}).get("rulename")
                or a.get("labels", {}).get("__alert_rule_uid__", "unknown check")
                for a in affected
            }
        )
        errors = sorted(
            {
                str(a.get("annotations", {}).get("Error"))
                for a in affected
                if a.get("annotations", {}).get("Error")
            }
        )
        if status == "resolved":
            summary = f"Data source {source}: query problem resolved"
        else:
            problem = "query failed" if name == "DatasourceError" else "returned no data"
            summary = f"Data source {source} {problem}; {len(checks)} checks affected"
        detail = "Affected checks:\n" + "\n".join(f"- {check}" for check in checks)
        if status != "resolved" and errors:
            detail += "\n\nQuery error:\n" + "\n".join(errors)
        notifications.append(
            {
                "labels": {"alertname": name, "datasource_uid": source, "severity": "warning"},
                "status": status,
                "annotations": {"summary": summary},
                "valueString": detail,
                "fingerprint": f"{name}-{source}",
                "startsAt": min(a.get("startsAt") or "" for a in affected),
            }
        )
    return notifications


def _alert_fields(alert: dict[str, Any]) -> tuple[str, str, str, str]:
    """Use truthful titles for firing and recovery notifications."""
    labels = alert.get("labels", {})
    name = labels.get("alertname", "unknown")
    summary = alert.get("annotations", {}).get("summary") or name
    human = human_alert_value(compact_value(alert.get("valueString", "")))
    status = alert.get("status", "firing")
    if status == "resolved":
        summary = f"Resolved: {summary}"
    return _banner_fields(status, labels.get("severity", "warning"), summary, human)


def _publish_one_alert(alert: dict[str, Any]) -> None:
    """Publish a notification to MQTT; external channels get one group digest."""
    labels = alert.get("labels", {})
    name = labels.get("alertname", "unknown")
    status = alert.get("status", "firing")
    _, mqtt_level, title, body = _alert_fields(alert)
    identity = alert.get("fingerprint") or name

    # ponytail: fire-and-forget publish; alerts missed while broker is down
    # are acceptable because rules keep firing state visible in Grafana UI.
    # Payload matches inverter-control / inverter-desktop banner schema.
    client.publish(
        NOTIFY_TOPIC,
        json.dumps(
            {
                "id": f"grafana-{identity}-{status}",
                "level": mqtt_level,
                "title": title,
                "body": body,
                "source": "grafana",
                "ts": datetime.now(UTC).isoformat(),
            }
        ),
    )


def _send_digest(alerts: list[dict[str, Any]]) -> None:
    """Preserve Grafana grouping across email and Telegram, including mixed states."""
    if not alerts:
        return
    fields = [_alert_fields(alert) for alert in alerts]
    if len(fields) == 1:
        level, _, summary, body = fields[0]
    else:
        level = next(
            candidate
            for candidate in ("critical", "warning", "info")
            if any(field[0] == candidate for field in fields)
        )
        firing = sum(a.get("status", "firing") != "resolved" for a in alerts)
        summary = f"{firing} active, {len(alerts) - firing} resolved monitoring notifications"
        body = "\n\n".join(f"{title}\n{detail}".strip() for _, _, title, detail in fields)
    name = alerts[0].get("labels", {}).get("alertname", "unknown")
    if SMTP_HOST and SMTP_TO:
        send_email(name, level, summary, body)
    if TG_BOT_TOKEN and TG_CHAT_IDS:
        # Telegram rejects messages over 4096 characters; keep one digest.
        send_telegram(name, level, summary, body[:3500])


def publish_alerts(payload: dict[str, Any]) -> int:
    """Map a Grafana webhook payload to MQTT notifications. Returns count."""
    received = payload.get("alerts") or []
    alerts = coalesce_datasource_alerts(received)
    for alert in alerts:
        _publish_one_alert(alert)
    _send_digest(alerts)

    # Retained snapshot of current alert states for late subscribers.
    snapshot = [
        {
            "name": a.get("labels", {}).get("alertname", "unknown"),
            "status": a.get("status", "firing"),
            "summary": a.get("annotations", {}).get("summary"),
            "value": compact_value(a.get("valueString", "")),
            "since": a.get("startsAt"),
        }
        for a in alerts
    ]
    client.publish(
        STATE_TOPIC, json.dumps({"updated": time.time(), "alerts": snapshot}), retain=True
    )
    return len(received)


class Handler(BaseHTTPRequestHandler):
    """HTTP endpoints for Grafana webhooks and liveness checks."""

    def do_POST(self) -> None:  # pylint: disable=invalid-name  # noqa: N802
        """Accept a Grafana webhook payload on /grafana."""
        if self.path != "/grafana":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            n = publish_alerts(payload)
            log.info("Webhook: %d alert(s), status=%s", n, payload.get("status"))
            self.send_response(200)
        except (ValueError, KeyError) as e:
            log.error("Bad webhook payload: %s", e)
            self.send_response(400)
        self.end_headers()

    def do_GET(self) -> None:  # pylint: disable=invalid-name  # noqa: N802
        """Liveness endpoint on /health."""
        if self.path != "/health":
            self.send_error(404)
            return
        body = json.dumps({"mqtt_connected": client.is_connected()}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:  # pylint: disable=arguments-differ
        """Silence per-request access logs; app logs cover it."""


if __name__ == "__main__":
    client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
    client.loop_start()
    log.info(
        "Listening :%s -> MQTT %s:%s topics %s/%s",
        LISTEN_PORT,
        MQTT_HOST,
        MQTT_PORT,
        NOTIFY_TOPIC,
        STATE_TOPIC,
    )
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()
