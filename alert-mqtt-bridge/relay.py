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
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


def _on_connect(_c, _u, _f, rc, *_props):
    if rc == 0:
        log.info("Connected to MQTT %s:%s", MQTT_HOST, MQTT_PORT)
        _connect_event.set()
    else:
        log.warning("MQTT connect failed rc=%s", rc)


def _on_disconnect(_c, _u, rc):
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
    msg.set_content(f"{summary}\n\n{value}\n")
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        log.info("Email sent: %s", name)
    except (OSError, smtplib.SMTPException) as e:
        log.error("Email send failed for %s: %s", name, e)


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


def publish_alerts(payload: dict) -> int:
    """Map a Grafana webhook payload to MQTT notifications. Returns count."""
    alerts = payload.get("alerts") or []
    count = 0
    for alert in alerts:
        labels = alert.get("labels", {})
        name = labels.get("alertname", "unknown")
        status = alert.get("status", "firing")
        summary = alert.get("annotations", {}).get("summary") or name
        value = alert.get("valueString", "")
        severity = labels.get("severity", "warning")

        if status == "resolved":
            level = "info"
            message = f"Grafana RESOLVED: {summary}"
        else:
            level = "critical" if severity == "critical" else "warning"
            message = f"Grafana: {summary}" + (f" [{value}]" if value else "")

        # ponytail: fire-and-forget publish; alerts missed while broker is down
        # are acceptable because rules keep firing state visible in Grafana UI.
        client.publish(
            NOTIFY_TOPIC,
            json.dumps({"id": f"grafana-{name}-{status}", "level": level, "message": message}),
        )
        if SMTP_HOST and SMTP_TO:
            send_email(name, level, summary, value)
        if TG_BOT_TOKEN and TG_CHAT_IDS:
            send_telegram(name, level, summary, value)
        count += 1

    # Retained snapshot of current alert states for late subscribers.
    snapshot = [
        {
            "name": a.get("labels", {}).get("alertname", "unknown"),
            "status": a.get("status", "firing"),
            "summary": a.get("annotations", {}).get("summary"),
            "value": a.get("valueString"),
            "since": a.get("startsAt"),
        }
        for a in alerts
    ]
    client.publish(
        STATE_TOPIC, json.dumps({"updated": time.time(), "alerts": snapshot}), retain=True
    )
    return count


class Handler(BaseHTTPRequestHandler):
    """HTTP endpoints for Grafana webhooks and liveness checks."""

    def do_POST(self):  # pylint: disable=invalid-name  # noqa: N802
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

    def do_GET(self):  # pylint: disable=invalid-name  # noqa: N802
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

    def log_message(self, fmt, *args):  # pylint: disable=arguments-differ
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
