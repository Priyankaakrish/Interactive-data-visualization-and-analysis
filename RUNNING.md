# How to run this project

## 1. Prerequisites

- **Python 3.9 or newer** (3.11 recommended)
- No database installation required — the project starts its own PostgreSQL 16

Check your version:

```bash
python --version
```

## 2. Install dependencies

From the project root:

```bash
pip install -r requirements.txt
```

On Windows, if `python` is not recognised, use `py -m pip install -r requirements.txt`.

A virtual environment is optional but tidier:

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

## 3. Check the data is in place

The source workbook should already be at:

```
data/raw/online_retail_II.xlsx
```

If it is missing, download it from
<https://archive.ics.uci.edu/dataset/502/online+retail+ii> and put it there.
Nothing else needs configuring — the ingest layer finds it by name.

No workbook? The project still runs:

```bash
python tools/generate_sample_retail.py
```

That writes a schema-identical sample with the same defect profile, so every
layer still has something real to work on.

## 4. Run the pipeline

```bash
python -m src.run_pipeline
```

That is the whole thing. It prints each of the seven stages as it goes:

```
1-2. DATASET -> PYTHON / PANDAS          reads 1,067,371 rows, profiles them
3.   DATA CLEANING & VALIDATION          quarantines 41,016, runs 25 rules
4.   POSTGRESQL                          loads the star schema, applies keys
5.   SQL ANALYTICS                       builds and reads 8 KPI views
6.   VISUALIZATION LIBRARY               dashboards, workbook, CSVs
7.   MONITORING                          run record, drift, health
```

**Expected runtime:** about 90 seconds. The very first run is slower — roughly
three minutes — because openpyxl has to parse the 45 MB workbook. That parse is
cached to `data/raw/.cache/`, so every run after it starts in under a second.

Useful flags:

```bash
python -m src.run_pipeline --no-dashboards      # skip HTML, just load and export
python -m src.run_pipeline --config other.yaml  # alternative configuration
```

## 5. Look at the results

| File | What it is |
|---|---|
| `outputs/retail_dashboard.html` | Business dashboard, 7 tabs — open in any browser |
| `outputs/monitoring_dashboard.html` | Pipeline health, data quality, drift |
| `outputs/Retail_KPI_Workbook.xlsx` | 10-sheet formatted stakeholder workbook |
| `data/processed/*.csv` | Every analytics view — the Power BI / Tableau folder source |

Both dashboards are self-contained: Plotly is inlined, so they work offline and
can be emailed as a single file.

## 6. Run the tests

```bash
pytest -q
```

29 tests covering the KPI maths, the cleaning decisions and the validation
rules. They use a tiny hand-computable fixture, so a failure names the number it
expected rather than sending you into a million rows.

## 7. Connect Power BI

**Option A — folder source (no database).** Get Data → Folder →
`data/processed`. Every analytics view is there as CSV.

**Option B — live database.** Set `database.mode: external` in `config.yaml`,
point `dsn` at your PostgreSQL, run the pipeline once to populate it, then in
Power BI use Get Data → PostgreSQL database. Import the `analytics` views and
the `core` dimensions.

Either way, load `powerbi/measures.dax` into a `_Measures` table.
`powerbi/MODEL.md` documents the relationships, page design and refresh order.

## 8. Using your own PostgreSQL instead of the embedded one

Edit `config.yaml`:

```yaml
database:
  mode: external
  dsn: "postgresql+psycopg2://user:password@localhost:5432/retail_bi"
```

Or set an environment variable, which overrides the file:

```bash
set DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/retail_bi   # Windows
export DATABASE_URL=postgresql+psycopg2://user:pass@localhost:5432/retail_bi # macOS/Linux
```

Create the database first (`CREATE DATABASE retail_bi;`). The pipeline creates
its own schemas, tables and views inside it.

---

## Troubleshooting

**"Could not start an embedded PostgreSQL"**
PostgreSQL must be able to `chmod` and delete inside its data directory, which
cloud-synced folders (OneDrive, Dropbox, Google Drive) and network shares often
forbid. Either move the project to a local path, or point the database
somewhere local:

```bash
set PGDATA_DIR=C:\pgdata\retail_bi       # Windows
export PGDATA_DIR=/tmp/retail_bi_pgdata  # macOS / Linux
```

The pipeline also falls back to a temp directory automatically and logs that it
did so.

**First run feels stuck**
It is parsing 1.07M rows out of Excel. Give it about three minutes; subsequent
runs use the cache.

**"No input found in data/raw"**
Either put `online_retail_II.xlsx` there, or run
`python tools/generate_sample_retail.py`.

**`ModuleNotFoundError: No module named 'src'`**
Run from the project root — the folder containing `config.yaml` — and use
`python -m src.run_pipeline`, not `python src/run_pipeline.py`.

**The load aborts with "ERROR-severity check(s) failed"**
That is the gate working as designed. The message names the failing rules and
row counts; `data/processed/quarantine.csv` shows the rows and the reason each
was rejected.
