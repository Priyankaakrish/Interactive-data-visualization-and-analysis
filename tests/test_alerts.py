"""Alerting must never raise into the pipeline it monitors."""
from __future__ import annotations

import json

from src.alerts import from_monitoring, notify


def test_log_sink_writes_json(tmp_path, monkeypatch) -> None:
    path = tmp_path / "alerts.jsonl"
    monkeypatch.setenv("ALERT_LOG_FILE", str(path))
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("ALERT_SMTP_HOST", raising=False)

    result = notify("RED", "pipeline failed", "3 ERROR checks")
    assert result["log"] is True

    record = json.loads(path.read_text().strip())
    assert record["level"] == "RED"
    assert record["title"] == "pipeline failed"


def test_unreachable_webhook_does_not_raise(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALERT_LOG_FILE", str(tmp_path / "a.jsonl"))
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "http://127.0.0.1:1/nope")
    result = notify("AMBER", "warnings present", "4 WARN checks")
    assert result["webhook"] is False
    assert result["log"] is True


def test_green_status_is_not_alerted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ALERT_LOG_FILE", str(tmp_path / "a.jsonl"))
    assert from_monitoring("GREEN", {}, 0.0) == {"skipped": True}
