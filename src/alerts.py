"""Alerting for the monitoring gate.

Three sinks, each enabled by the presence of its environment variable, so the
module is inert until configured and never raises into the caller. An alerting
path that can crash the pipeline it monitors is worse than no alerting.

    ALERT_WEBHOOK_URL   Slack / Teams / Discord incoming webhook
    ALERT_SMTP_HOST     plus ALERT_SMTP_PORT, ALERT_EMAIL_TO, ALERT_EMAIL_FROM
    ALERT_LOG_FILE      newline-delimited JSON, always safe
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Literal

log = logging.getLogger("alerts")

Level = Literal["GREEN", "AMBER", "RED"]

_COLOUR = {"GREEN": "#2d7d46", "AMBER": "#e08a1e", "RED": "#c0392b"}
_EMOJI = {"GREEN": ":white_check_mark:", "AMBER": ":warning:", "RED": ":rotating_light:"}


def _webhook(level: Level, title: str, body: str) -> bool:
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        return False

    payload = {
        "text": f"{_EMOJI.get(level, '')} *{title}*",
        "attachments": [{
            "color": _COLOUR.get(level, "#6b7480"),
            "text": body,
            "footer": "Online Retail II BI pipeline",
            "ts": int(datetime.now(timezone.utc).timestamp()),
        }],
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        log.warning("webhook alert failed: %s", exc)
        return False


def _email(level: Level, title: str, body: str) -> bool:
    host = os.getenv("ALERT_SMTP_HOST", "").strip()
    to = os.getenv("ALERT_EMAIL_TO", "").strip()
    if not host or not to:
        return False

    message = EmailMessage()
    message["Subject"] = f"[{level}] {title}"
    message["From"] = os.getenv("ALERT_EMAIL_FROM", "retail-bi@localhost")
    message["To"] = to
    message.set_content(body)

    try:
        port = int(os.getenv("ALERT_SMTP_PORT", "25"))
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            user = os.getenv("ALERT_SMTP_USER")
            password = os.getenv("ALERT_SMTP_PASSWORD")
            if user and password:
                smtp.starttls()
                smtp.login(user, password)
            smtp.send_message(message)
        return True
    except Exception as exc:
        log.warning("email alert failed: %s", exc)
        return False


def _logfile(level: Level, title: str, body: str) -> bool:
    path = Path(os.getenv("ALERT_LOG_FILE", "outputs/alerts.jsonl"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "title": title,
            "body": body,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
        return True
    except Exception as exc:
        log.warning("log alert failed: %s", exc)
        return False


def notify(level: Level, title: str, body: str) -> dict:
    """Send to every configured sink. Never raises."""
    sent = {
        "webhook": _webhook(level, title, body),
        "email": _email(level, title, body),
        "log": _logfile(level, title, body),
    }
    if not any(sent.values()):
        log.info("[%s] %s\n%s", level, title, body)
    return sent


def from_monitoring(status: str, failing: dict, drift_pct: float = 0.0) -> dict:
    """Convenience wrapper for the pipeline's own status object."""
    level: Level = "RED" if status == "RED" else ("AMBER" if status == "AMBER" else "GREEN")
    if level == "GREEN":
        return {"skipped": True}
    return notify(
        level=level,
        title=f"Retail BI pipeline finished {status}",
        body=f"Failing checks: {failing or 'none'}\nMax row drift: {drift_pct}%",
    )
