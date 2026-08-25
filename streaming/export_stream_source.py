"""Export a replayable slice of the warehouse for the Kafka producer."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import pandas as pd

from src import db
from src.config import PROJECT_ROOT, load_config

QUERY = """
SELECT f.invoice_no,
       f.stock_code,
       p.description,
       f.quantity,
       f.unit_price,
       f.line_revenue,
       c.customer_id,
       g.country,
       f.is_cancellation,
       d.full_date AS event_date
FROM   core.fact_sales                AS f
INNER  JOIN core.vw_dim_date_flat     AS d ON d.date_key     = f.date_key
INNER  JOIN core.vw_dim_country_flat  AS g ON g.country_key  = f.country_key
INNER  JOIN core.vw_dim_product_flat  AS p ON p.product_key  = f.product_key
LEFT   JOIN core.vw_dim_customer_flat AS c ON c.customer_key = f.customer_key
ORDER  BY d.full_date DESC, f.invoice_no
LIMIT  {rows}
"""


def spread_within_day(frame: pd.DataFrame) -> pd.DataFrame:
    """Give each line a time of day derived from its invoice number.

    dim_date is day-grain, so every row on a date shares a timestamp. Event-time
    windowing needs them spread out. Hashing the invoice keeps it deterministic -
    the same export always produces the same stream.
    """
    seconds = frame["invoice_no"].astype(str).map(
        lambda s: int(hashlib.md5(s.encode()).hexdigest()[:8], 16) % 86400)
    frame["event_time"] = pd.to_datetime(frame["event_date"]) + pd.to_timedelta(seconds, unit="s")
    return frame.drop(columns=["event_date"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Export replay source for the Kafka producer")
    ap.add_argument("--rows", type=int, default=250_000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    out = Path(args.out) if args.out else PROJECT_ROOT / "data" / "stream" / "source.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    cfg = load_config(None)
    with db.connect(cfg) as engine:
        frame = db.fetch(engine, QUERY.format(rows=args.rows))

    frame = spread_within_day(frame)
    frame = frame.sort_values("event_time").reset_index(drop=True)
    frame.to_csv(out, index=False)

    print(f"  wrote {len(frame):,} rows -> {out}")
    print(f"  event window: {frame['event_time'].min()} .. {frame['event_time'].max()}")


if __name__ == "__main__":
    main()
