"""Evaluate the business-rule alert views and dispatch what fires."""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field

import pandas as pd

# The database and alert sinks are imported inside the functions that use them,
# so the grouping and rendering logic can be tested with no warehouse.
log = logging.getLogger("business_alerts")

# An alert listing 400 dead products is noise; the top few plus a count is not.
MAX_DETAIL_ROWS = 5

ALERT_TITLES = {
    "revenue_drop": "Revenue decline",
    "return_spike": "Return-rate spike",
    "dead_stock": "Dead stock",
    "demand_surge": "Demand surge (stockout risk proxy)",
    "churn_risk": "Customer churn risk",
}


@dataclass
class AlertGroup:
    alert_key: str
    severity: str
    rows: pd.DataFrame
    sent: dict = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def title(self) -> str:
        return ALERT_TITLES.get(self.alert_key, self.alert_key)

    def render(self) -> str:
        lines = [f"{self.count} occurrence(s) of {self.title}.", ""]
        for _, r in self.rows.head(MAX_DETAIL_ROWS).iterrows():
            lines.append(f"  - {r['subject']}")
            lines.append(f"    {r['detail']}")
        if self.count > MAX_DETAIL_ROWS:
            lines.append(f"  ... and {self.count - MAX_DETAIL_ROWS} more")
        return "\n".join(lines)


def fetch_alerts(engine, alert_key: str | None = None) -> pd.DataFrame:
    """Read the rollup view. Returns an empty frame if the schema is absent."""
    from src import db

    try:
        frame = db.fetch(engine, "SELECT * FROM alerting.vw_all_alerts")
    except Exception as exc:
        log.warning("alerting schema not available (%s)", exc)
        return pd.DataFrame()
    if alert_key and not frame.empty:
        frame = frame[frame["alert_key"] == alert_key]
    return frame


def group(frame: pd.DataFrame) -> list[AlertGroup]:
    """One message per rule, not per row - five emails beats four hundred."""
    if frame.empty:
        return []
    out = []
    for key, rows in frame.groupby("alert_key", sort=False):
        out.append(AlertGroup(alert_key=str(key),
                              severity=str(rows["severity"].iloc[0]),
                              rows=rows.reset_index(drop=True)))
    out.sort(key=lambda g: (0 if g.severity == "RED" else 1, -g.count))
    return out


def run(config_path: str | None = None, dry_run: bool = False,
        alert_key: str | None = None) -> list[AlertGroup]:
    from src import db
    from src.alerts import notify
    from src.config import load_config

    cfg = load_config(config_path)
    with db.connect(cfg) as engine:
        frame = fetch_alerts(engine, alert_key)

    groups = group(frame)
    if not groups:
        log.info("no business alerts firing")
        return []

    for g in groups:
        body = g.render()
        if dry_run:
            print(f"\n[{g.severity}] {g.title}\n{body}")
            continue
        g.sent = notify(level=g.severity, title=f"{g.title} ({g.count})", body=body)
    return groups


def summary_table(groups: list[AlertGroup]) -> pd.DataFrame:
    return pd.DataFrame([
        {"alert_key": g.alert_key, "severity": g.severity,
         "count": g.count, "title": g.title}
        for g in groups
    ])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    ap = argparse.ArgumentParser(description="Evaluate business-rule alerts")
    ap.add_argument("--config", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--key", default=None)
    args = ap.parse_args()

    groups = run(args.config, dry_run=args.dry_run, alert_key=args.key)
    print("\n  BUSINESS ALERTS" + ("  (dry run)" if args.dry_run else ""))
    if not groups:
        print("  nothing firing")
        return
    print(summary_table(groups).to_string(index=False))


if __name__ == "__main__":
    main()
