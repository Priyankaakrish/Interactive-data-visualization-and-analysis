"""PostgreSQL access layer - stage 3.

Two connection modes behind one function:

* ``embedded`` starts a self-contained PostgreSQL through pgserver, so the
  project runs on a machine with no database installed. Useful for a fresh
  clone, CI, and reviewers who will not install a server to read your code.
* ``external`` connects to a real instance via the DSN in config.yaml or the
  DATABASE_URL environment variable.

Both return a SQLAlchemy engine, so nothing else in the codebase knows or
cares which one is in play.
"""
from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from .config import PROJECT_ROOT, Config

log = logging.getLogger(__name__)

SQL_DIR = PROJECT_ROOT / "sql"


# --------------------------------------------------------------------------
# Connection
# --------------------------------------------------------------------------
def _start_embedded(pgserver, cfg: Config):
    """Initialise and start the bundled PostgreSQL.

    PostgreSQL refuses to run on a data directory it cannot chmod to 0700, and
    initdb must be able to delete files inside it. Cloud-synced folders, network
    shares and some container mounts do not permit either. Rather than fail with
    an opaque initdb error, fall back to a local temp directory and say so.
    """
    import os
    import tempfile

    candidates: list[Path] = []
    if os.environ.get("PGDATA_DIR"):
        candidates.append(Path(os.environ["PGDATA_DIR"]))
    configured = cfg.db.get("embedded_dir", ".pgdata")
    candidates.append(Path(configured) if Path(configured).is_absolute()
                      else PROJECT_ROOT / configured)
    candidates.append(Path(tempfile.gettempdir()) / "online_retail_bi_pgdata")

    last_error: Exception | None = None
    for data_dir in candidates:
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            log.info("starting embedded PostgreSQL in %s", data_dir)
            server = pgserver.get_server(str(data_dir), cleanup_mode=None)
            return server, server.get_uri()
        except Exception as exc:                        # noqa: BLE001
            last_error = exc
            log.warning("cannot host the database in %s (%s)", data_dir,
                        str(exc).splitlines()[-1][:120])

    raise RuntimeError(
        "Could not start an embedded PostgreSQL in any candidate directory "
        f"({', '.join(str(c) for c in candidates)}). Set PGDATA_DIR to a local "
        "path, or use database.mode: external with your own server."
    ) from last_error


@contextmanager
def connect(cfg: Config):
    """Yield an Engine, starting an embedded server first if configured."""
    mode = cfg.db.get("mode", "external")
    server = None

    if mode == "embedded":
        try:
            import pgserver
        except ImportError as exc:                       # pragma: no cover
            raise RuntimeError(
                "database.mode is 'embedded' but pgserver is not installed.\n"
                "Run `pip install pgserver`, or set database.mode: external "
                "and point database.dsn at your own PostgreSQL."
            ) from exc

        server, url = _start_embedded(pgserver, cfg)
    else:
        url = cfg.dsn
        if not url:
            raise RuntimeError(
                "No database DSN. Set database.dsn in config.yaml or export "
                "DATABASE_URL."
            )

    engine = sa.create_engine(url, future=True)
    try:
        with engine.connect() as c:
            version = c.execute(sa.text("select version()")).scalar()
        log.info("connected: %s", str(version).split(",")[0])
        yield engine
    finally:
        engine.dispose()


def server_version(engine: Engine) -> str:
    with engine.connect() as c:
        return str(c.execute(sa.text("select version()")).scalar()).split(",")[0]


# --------------------------------------------------------------------------
# DDL execution
# --------------------------------------------------------------------------
def _split_statements(sql: str) -> list[str]:
    """Split a script on semicolons, respecting $$-quoted function bodies."""
    chunks, buf, in_dollar = [], [], False
    for line in sql.splitlines():
        if line.count("$$") % 2 == 1:
            in_dollar = not in_dollar
        buf.append(line)
        if not in_dollar and line.rstrip().endswith(";"):
            chunks.append("\n".join(buf))
            buf = []
    if buf:
        chunks.append("\n".join(buf))
    return [c.strip() for c in chunks if c.strip() and not _is_comment_only(c)]


def _is_comment_only(chunk: str) -> bool:
    body = re.sub(r"/\*.*?\*/", "", chunk, flags=re.S)
    body = "\n".join(ln for ln in body.splitlines() if not ln.strip().startswith("--"))
    return not body.strip()


def run_sql_file(engine: Engine, path: str | Path, params: dict | None = None) -> int:
    """Execute a .sql script. Returns the number of statements run."""
    path = Path(path)
    sql = path.read_text(encoding="utf-8")
    if params:
        for key, value in params.items():
            sql = sql.replace(f"{{{{{key}}}}}", str(value))

    statements = _split_statements(sql)
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(sa.text(stmt))
    log.info("ran %s (%s statements)", path.name, len(statements))
    return len(statements)


def run_sql_dir(engine: Engine, directory: str | Path = SQL_DIR,
                params: dict | None = None, pattern: str = "*.sql") -> list[str]:
    """Execute every script in a directory, in filename order."""
    directory = Path(directory)
    ran = []
    for path in sorted(directory.glob(pattern)):
        run_sql_file(engine, path, params)
        ran.append(path.name)
    return ran


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------
def _sql_dtypes(df: pd.DataFrame) -> dict:
    """Pin the types that pandas would otherwise infer badly.

    A surrogate key with any NULL in it becomes float64 in pandas, which
    to_sql writes as DOUBLE PRECISION - and a foreign key from a double to a
    bigint is rejected by PostgreSQL. Forcing every *_key column to BIGINT is
    what lets the constraints in 02_constraints.sql apply cleanly.
    """
    money_like = ("revenue", "price", "value", "monetary", "amount")
    mapping = {}
    for col in df.columns:
        low = col.lower()
        if col.endswith("_key") and pd.api.types.is_numeric_dtype(df[col]):
            mapping[col] = sa.BigInteger()
        elif pd.api.types.is_bool_dtype(df[col]):
            mapping[col] = sa.Boolean()
        elif pd.api.types.is_datetime64_any_dtype(df[col]):
            mapping[col] = sa.DateTime()
        elif (any(k in low for k in money_like)
              and pd.api.types.is_numeric_dtype(df[col])):
            # Money must be NUMERIC, never float: PostgreSQL has no
            # round(double precision, int), and binary floats should not be
            # accumulating currency in the first place.
            mapping[col] = sa.Numeric(14, 2)
    return mapping


def load_frame(engine: Engine, df: pd.DataFrame, schema: str, table: str,
               chunksize: int = 20000, if_exists: str = "replace") -> int:
    """Write a DataFrame to PostgreSQL and return the row count."""
    df = df.copy()

    # Nullable surrogate keys must stay integral, not drift to float.
    for col in df.columns:
        if col.endswith("_key") and pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].astype("Int64")

    dtypes = _sql_dtypes(df)

    # pandas nullable extension dtypes do not round-trip through psycopg2.
    for col in df.columns:
        if str(df[col].dtype) in ("string", "Int64", "Int32", "boolean", "Float64"):
            df[col] = df[col].astype(object).where(df[col].notna(), None)

    # Create the empty table with the right types, then stream the rows in with
    # COPY. Row-by-row INSERT is orders of magnitude slower, and multi-row
    # INSERT breaks against PostgreSQL's 65,535 bound-parameter ceiling once the
    # real 1.07M-row dataset is in play.
    df.head(0).to_sql(table, engine, schema=schema, if_exists=if_exists,
                      index=False, dtype=dtypes)
    _copy_into(engine, df, schema, table)

    log.info("loaded %s.%s (%s rows)", schema, table, len(df))
    return len(df)


def _copy_into(engine: Engine, df: pd.DataFrame, schema: str, table: str) -> None:
    """Bulk-load a DataFrame with PostgreSQL COPY."""
    import csv
    import io

    buf = io.StringIO()
    df.to_csv(buf, index=False, header=False, na_rep="\\N",
              quoting=csv.QUOTE_MINIMAL, date_format="%Y-%m-%d %H:%M:%S")
    buf.seek(0)

    columns = ", ".join(f'"{c}"' for c in df.columns)
    sql = (f'COPY {schema}."{table}" ({columns}) '
           f"FROM STDIN WITH (FORMAT csv, NULL '\\N')")

    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cur:
            cur.copy_expert(sql, buf)
        raw_conn.commit()
    finally:
        raw_conn.close()


def load_star(engine: Engine, star: dict[str, pd.DataFrame], schema: str,
              chunksize: int = 20000) -> pd.DataFrame:
    """Load every table of the star schema; return a load manifest."""
    manifest = []
    # Dimensions first so the foreign keys added in SQL can be validated.
    order = ["dim_date", "dim_country", "dim_product", "dim_customer", "fact_sales"]
    for name in order + [k for k in star if k not in order]:
        if name not in star:
            continue
        rows = load_frame(engine, star[name], schema, name, chunksize)
        manifest.append({"table_name": name, "rows_loaded": rows,
                         "columns": star[name].shape[1]})
    return pd.DataFrame(manifest)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
def fetch(engine: Engine, query: str, params: dict | None = None) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(sa.text(query), conn, params=params or {})


def fetch_view(engine: Engine, schema: str, view: str) -> pd.DataFrame:
    return fetch(engine, f'select * from {schema}."{view}"')


def table_counts(engine: Engine, schema: str) -> pd.DataFrame:
    """Actual row counts per table - used by the monitoring layer."""
    tables = fetch(engine, """
        select table_name
        from information_schema.tables
        where table_schema = :schema and table_type = 'BASE TABLE'
        order by table_name
    """, {"schema": schema})

    rows = []
    for t in tables["table_name"]:
        n = fetch(engine, f'select count(*) as n from {schema}."{t}"')["n"].iloc[0]
        rows.append({"table_name": t, "row_count": int(n)})
    return pd.DataFrame(rows)
