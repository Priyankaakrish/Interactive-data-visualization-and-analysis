"""Replay invoice lines onto a Kafka topic at a controllable rate.

The source data is historical, so wall-clock replay would take two years. The
producer instead compresses event time by a speed factor and stamps each
message with both its original event time and the emit time, so the consumer
can do genuine event-time windowing while the demo runs in minutes.

    python -m streaming.producer --speed 20000 --rate 400
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

import pandas as pd

try:
    from kafka import KafkaProducer
    from kafka.errors import NoBrokersAvailable
except ImportError:  # pragma: no cover
    KafkaProducer = None
    NoBrokersAvailable = Exception

DEFAULT_SOURCE = Path(os.getenv("STREAM_SOURCE", "data/stream/source.csv"))
DEFAULT_BROKER = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
DEFAULT_TOPIC = os.getenv("KAFKA_TOPIC", "retail.invoices")

_running = True


def _stop(*_args) -> None:
    global _running
    _running = False
    print("\n  stop requested - draining")


def build_producer(broker: str, retries: int = 10) -> KafkaProducer:
    if KafkaProducer is None:
        raise SystemExit("kafka-python is not installed. pip install kafka-python")
    for attempt in range(1, retries + 1):
        try:
            return KafkaProducer(
                bootstrap_servers=broker,
                value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                key_serializer=lambda k: str(k).encode("utf-8"),
                acks="all",
                linger_ms=20,
                retries=5,
            )
        except NoBrokersAvailable:
            wait = min(2 ** attempt, 15)
            print(f"  broker not ready ({attempt}/{retries}) - retrying in {wait}s")
            time.sleep(wait)
    raise SystemExit(f"could not reach Kafka at {broker}")


def load_source(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(
            f"source not found: {path}\n"
            "  run:  python -m streaming.export_stream_source"
        )
    frame = pd.read_csv(path, parse_dates=["event_time"])
    return frame.sort_values("event_time").reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay retail invoice lines to Kafka")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--broker", default=DEFAULT_BROKER)
    ap.add_argument("--topic", default=DEFAULT_TOPIC)
    ap.add_argument("--speed", type=float, default=20_000.0,
                    help="event-time compression factor")
    ap.add_argument("--rate", type=float, default=400.0,
                    help="hard ceiling on messages per second")
    ap.add_argument("--limit", type=int, default=0, help="stop after N messages")
    ap.add_argument("--loop", action="store_true", help="restart when the source is exhausted")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    frame = load_source(args.source)
    print(f"  source     {args.source}  ({len(frame):,} rows)")
    print(f"  broker     {args.broker}")
    print(f"  topic      {args.topic}")
    print(f"  speed      {args.speed:,.0f}x   ceiling {args.rate:,.0f} msg/s")

    producer = build_producer(args.broker)
    min_gap = 1.0 / args.rate if args.rate > 0 else 0.0

    sent = 0
    started = time.time()
    while _running:
        prev_event = None
        for row in frame.itertuples(index=False):
            if not _running:
                break

            if prev_event is not None:
                delta = (row.event_time - prev_event).total_seconds() / args.speed
                time.sleep(max(min(delta, 2.0), min_gap))
            else:
                time.sleep(min_gap)
            prev_event = row.event_time

            payload = {
                "invoice_no": str(row.invoice_no),
                "stock_code": str(row.stock_code),
                "description": None if pd.isna(row.description) else str(row.description),
                "quantity": int(row.quantity),
                "unit_price": float(row.unit_price),
                "line_revenue": float(row.line_revenue),
                "customer_id": None if pd.isna(row.customer_id) else str(row.customer_id),
                "country": str(row.country),
                "is_cancellation": bool(row.is_cancellation),
                "event_time": row.event_time.isoformat(),
            }
            producer.send(args.topic, key=payload["invoice_no"], value=payload)
            sent += 1

            if sent % 2_000 == 0:
                rate = sent / max(time.time() - started, 1e-6)
                print(f"  sent {sent:>9,}   {rate:7.0f} msg/s   event_time {row.event_time}")

            if args.limit and sent >= args.limit:
                _stop()
                break

        if not args.loop:
            break

    producer.flush(timeout=30)
    producer.close(timeout=30)
    elapsed = time.time() - started
    print(f"\n  delivered {sent:,} messages in {elapsed:.1f}s "
          f"({sent / max(elapsed, 1e-6):,.0f} msg/s)")


if __name__ == "__main__":
    sys.exit(main())
