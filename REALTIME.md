# Real-Time Extension

Adds four production capabilities to the batch platform: a streaming ingestion
path, a REST API, incremental loading with orchestration, and alerting with CI.

Nothing here replaces the batch warehouse. The reconciled figures still come
from `core.fact_sales`; the streaming path answers a different question, and
the two are kept in separate schemas so a streaming outage can never corrupt a
number someone has already signed off.

---

## What was added

```
streaming/
  export_stream_source.py   one-off: export a replayable slice of the warehouse
  producer.py               replay invoice lines onto a Kafka topic
  spark_consumer.py         Structured Streaming -> windowed KPIs -> PostgreSQL
api/
  main.py                   FastAPI app, lifespan model loading
  settings.py               environment-driven configuration
  models.py                 Pydantic v2 response contracts
  store.py                  in-memory extract store
  db.py                     optional stream connection
  util.py                   column-name tolerance helpers
  routers/                  health, kpi, products, customers, live, admin
orchestration/
  incremental.py            watermark load with UPSERT merge
  flow.py                   Prefect flow: retries, history, alerting
src/
  alerts.py                 webhook / email / file sinks
sql/
  07_streaming.sql          stream schema, live tables, rollup views
deployment/
  Dockerfile                multi-stage API image, non-root, healthcheck
  Dockerfile.producer       lightweight replay producer
  docker-compose.yml        kafka, spark, postgres, producer, api
tests/                      20 tests, no warehouse or broker required
.github/workflows/ci.yml    tests, lint, SQL validation, image build
```

---

## Quick start

```bash
pip install -r requirements-realtime.txt
```

**1. Export the replay source** (once, against your existing warehouse):

```bash
python -m streaming.export_stream_source --rows 250000
```

Writes `data/stream/source.csv`. The producer reads this rather than the
warehouse, so the streaming stack stays up even when the warehouse is down.

**2. Start the stack:**

```bash
docker compose -f deployment/docker-compose.yml up -d --build
```

Six services come up: `postgres`, `kafka`, `kafka-init`, `spark`, `producer`,
`api`. Spark downloads its Kafka and JDBC connectors on first run, so the first
start takes a few minutes; subsequent starts are quick.

**3. Watch it work:**

```bash
docker compose -f deployment/docker-compose.yml logs -f spark
curl http://localhost:8000/api/v1/live/summary
```

Open http://localhost:8000/docs for the interactive API.

---

## The streaming path

```
source.csv -> producer -> Kafka topic -> Spark Structured Streaming -> PostgreSQL
                                              (event-time windows)      stream.live_kpi
```

**Event time, not processing time.** The source spans two years, so wall-clock
replay would take two years. The producer compresses event time by a speed
factor (default 20,000x) while stamping each message with its original
timestamp. Spark then windows on that original timestamp, so the aggregation is
genuinely event-time based even though the demo runs in minutes.

**Watermarking.** A two-hour watermark bounds how long Spark waits for late
messages before finalising a window. Without one, the state store grows without
limit and the job eventually dies — the classic streaming failure.

**Idempotent writes.** Structured Streaming can redeliver a micro-batch after a
failure. An append would double-count it. Each batch is written to a staging
table and merged into the target on the window key, so a redelivery overwrites
rather than duplicates:

```sql
INSERT INTO stream.live_kpi (...) SELECT ... FROM stream._stg_live_kpi
ON CONFLICT (window_start, window_end) DO UPDATE SET ...
```

That single detail is the difference between a streaming figure you can quote
and one you cannot.

---

## The API

`/kpi`, `/products` and `/customers` serve the **reconciled batch extracts**.
`/live` serves the **streaming aggregates**. They are deliberately separate
endpoints: the batch figures are final, the live figures are approximate and
moving, and merging them would invite someone to compare the two and conclude
one is broken.

| Endpoint | Method | Returns |
|---|---|---|
| `/api/v1/health` | GET | Loaded extracts, stream connectivity, uptime |
| `/api/v1/kpi/summary` | GET | The ten headline figures |
| `/api/v1/kpi/monthly` | GET | Monthly revenue series |
| `/api/v1/kpi/countries` | GET | Revenue by country |
| `/api/v1/products/top` | GET | Top products, filterable by ABC class |
| `/api/v1/products/{code}` | GET | One product |
| `/api/v1/customers/segments` | GET | RFM segment sizes |
| `/api/v1/customers/top` | GET | Highest-value customers |
| `/api/v1/customers/{id}/rfm` | GET | One customer's RFM scores |
| `/api/v1/live/summary` | GET | Rolling streaming KPIs with lag |
| `/api/v1/live/windows` | GET | Recent windows |
| `/api/v1/live/countries` | GET | Live revenue by country |
| `/api/v1/admin/reload` | POST | Re-read the extract folder |

**Lifespan loading.** Extracts are read once at startup, not per request, so
p95 latency stays flat and a refresh mid-request cannot produce a half-updated
response. `/admin/reload` picks up a new pipeline run without a restart.

**Graceful degradation.** If the stream database is unreachable, `/live/summary`
returns `streaming: false` rather than a 500, and `/health` reports it. A
polling dashboard degrades quietly instead of showing an error banner.

Run it natively without Docker:

```bash
uvicorn api.main:app --reload --port 8000
```

---

## Incremental loading

Full refresh re-reads 1,067,371 rows and rebuilds in ~138 seconds. Fine
nightly, wasteful hourly.

```bash
python -m orchestration.incremental --dry-run   # report what would load
python -m orchestration.incremental             # load it
```

The watermark is `MAX(full_date)` in the fact table. Only rows newer than that
are cleaned, validated and merged. The merge is an UPSERT on `line_key`, not an
append — a corrected invoice re-delivered with the same key updates in place,
where an append would leave both versions and silently inflate revenue.

The validation gate still runs on the incremental slice. An ERROR-severity
failure aborts the merge, so partial bad data cannot leak in through the faster
path.

---

## Orchestration

```bash
prefect server start                  # terminal 1
python -m orchestration.flow          # run once
python -m orchestration.flow --serve  # schedule hourly
```

The flow chains three tasks — incremental load, extract refresh, monitoring
check — with retries on the first two. It alerts when the monitoring gate
returns AMBER or RED. Without Prefect installed the decorators degrade to plain
function calls, so the flow still runs.

---

## Alerting

Configured entirely by environment variable; inert until set, and it never
raises into the pipeline it monitors.

```bash
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/...
ALERT_SMTP_HOST=smtp.gmail.com
ALERT_EMAIL_TO=you@example.com
ALERT_LOG_FILE=outputs/alerts.jsonl
```

The file sink always works, so there is a record even when the network is down.

---

## Tests and CI

```bash
pytest tests/ -v
```

Twenty tests, no warehouse and no broker required — the fixtures are synthetic,
which is what lets CI run in seconds on a clean runner. The cleaning tests
assert the decisions the project's credibility rests on: duplicates removed,
cancellations negative, service lines retained, guest rows kept for revenue, and
the identity `net = gross - returns`.

GitHub Actions runs four jobs on every push: tests on Python 3.11 and 3.12,
ruff lint, `07_streaming.sql` applied against a real PostgreSQL service
container, and a Docker image build with compose validation.

---

## Operational notes

**Ports.** The compose PostgreSQL binds host port **5433**, not 5432, so it
cannot clash with the embedded `pgserver` instance the batch pipeline uses.

**Kafka from the host.** The broker advertises an EXTERNAL listener on 29092,
so a natively-run producer works:

```bash
python -m streaming.producer --broker localhost:29092 --speed 20000
```

**Spark first run.** `--packages` pulls the Kafka and PostgreSQL connectors from
Maven Central. The Ivy cache is on a volume, so this cost is paid once.

**Tear down:**

```bash
docker compose -f deployment/docker-compose.yml down -v
```

---

## Limitations, stated plainly

- The stream replays historical data. It is a faithful demonstration of
  streaming mechanics, not a live feed from a trading system — there is no such
  feed for this dataset.
- `approx_count_distinct` is used for the live order count. Exact distinct
  counting in a streaming aggregation is expensive and unnecessary for a figure
  that is explicitly approximate.
- The API serves published extracts rather than querying the warehouse
  directly. That keeps it fast and decoupled, at the cost of being as fresh as
  the last pipeline run. Row-level security therefore does **not** apply to the
  batch endpoints; if you need per-user filtering there, point the API at
  PostgreSQL and set the session role per request.
- Single-broker Kafka with replication factor 1. Correct for a development
  stack, not a production topology.
