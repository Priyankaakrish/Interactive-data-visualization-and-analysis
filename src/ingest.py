"""Ingest - stage 1.

Reads Online Retail II into a single tidy frame. Three sources are tried in
order, and all three produce the identical column contract:

1. `online_retail_II.xlsx` in data/raw - the real 43.5 MB UCI download. Both
   worksheets ("Year 2009-2010" and "Year 2010-2011") are read and stacked.
2. Any `*.csv` in data/raw with the same columns.
3. The generated sample, if `source.allow_sample_fallback` is true.

The two published versions of this dataset use different column names -
Online Retail I ships `InvoiceNo` / `UnitPrice` / `CustomerID`, while Online
Retail II ships `Invoice` / `Price` / `Customer ID`. Both are normalised here
so nothing downstream has to care which file the user happened to download.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

# Canonical internal names -> every spelling seen in the wild.
COLUMN_ALIASES: dict[str, list[str]] = {
    "invoice_no":   ["invoice", "invoiceno", "invoice_no", "invoice no"],
    "stock_code":   ["stockcode", "stock_code", "stock code"],
    "description":  ["description", "desc"],
    "quantity":     ["quantity", "qty"],
    "invoice_date": ["invoicedate", "invoice_date", "invoice date"],
    "unit_price":   ["price", "unitprice", "unit_price", "unit price"],
    "customer_id":  ["customer id", "customerid", "customer_id"],
    "country":      ["country"],
}

REQUIRED = list(COLUMN_ALIASES)


class SchemaError(RuntimeError):
    """Raised when the input does not match the Online Retail II contract."""


def normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map whatever the file called its columns onto the canonical names."""
    lookup = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for col in df.columns:
            if str(col).strip().lower() in aliases:
                lookup[col] = canonical
                break

    out = df.rename(columns=lookup)
    missing = [c for c in REQUIRED if c not in out.columns]
    if missing:
        raise SchemaError(
            f"Input is missing required column(s): {', '.join(missing)}.\n"
            f"Found: {list(df.columns)}\n"
            "Expected the Online Retail II schema (Invoice, StockCode, "
            "Description, Quantity, InvoiceDate, Price, Customer ID, Country)."
        )
    return out[REQUIRED]


def _coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    df["invoice_no"] = df["invoice_no"].astype("string").str.strip()
    df["stock_code"] = df["stock_code"].astype("string").str.strip()
    df["description"] = df["description"].astype("string")
    df["country"] = df["country"].astype("string").str.strip()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["unit_price"] = pd.to_numeric(df["unit_price"], errors="coerce")
    df["invoice_date"] = pd.to_datetime(df["invoice_date"], errors="coerce")
    # Customer ID is an identifier, not a measure - never let it become a float
    # with a trailing .0, which is the classic way this dataset breaks joins.
    df["customer_id"] = (
        pd.to_numeric(df["customer_id"], errors="coerce")
          .astype("Int64").astype("string")
    )
    return df


def read_excel_cached(path: Path) -> tuple[pd.DataFrame, bool]:
    """Read the workbook, caching the parsed result as CSV.

    The real UCI workbook is 45 MB and takes openpyxl close to two minutes to
    parse - per run, every run, for a file that never changes. The parsed frame
    is therefore cached alongside it and reused until the workbook's
    modification time moves. First run pays the cost; every run after it starts
    in under a second.
    """
    cache = path.parent / ".cache" / f"{path.stem}.parsed.csv"
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        log.info("using cached parse of %s", path.name)
        return pd.read_csv(cache, low_memory=False), True

    df = read_excel(path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    log.info("cached parsed workbook to %s", cache)
    return df, False


def read_excel(path: Path) -> pd.DataFrame:
    """Read every worksheet in the UCI workbook and stack them."""
    log.info("reading %s", path.name)
    sheets = pd.read_excel(path, sheet_name=None, engine="openpyxl")
    frames = []
    for sheet_name, sheet in sheets.items():
        sheet = normalise_columns(sheet)
        sheet["source_sheet"] = sheet_name
        frames.append(sheet)
        log.info("  sheet %-18s %s rows", sheet_name, len(sheet))
    return pd.concat(frames, ignore_index=True)


def read_csv(path: Path) -> pd.DataFrame:
    log.info("reading %s", path.name)
    df = normalise_columns(pd.read_csv(path, low_memory=False))
    df["source_sheet"] = path.stem
    return df


def load_transactions(cfg) -> tuple[pd.DataFrame, dict[str, object]]:
    """Return (transactions, provenance)."""
    raw_dir = cfg.raw_dir
    excel = raw_dir / cfg.excel_name

    if excel.exists():
        df, from_cache = read_excel_cached(excel)
        df = normalise_columns(df) if "invoice_no" not in df.columns else df
        provenance = {"source": excel.name,
                      "kind": "excel (cached)" if from_cache else "excel",
                      "bytes": excel.stat().st_size, "is_real_dataset": True}
    else:
        csvs = sorted(p for p in raw_dir.glob("*.csv") if not p.name.startswith("."))
        if csvs:
            df = pd.concat([read_csv(p) for p in csvs], ignore_index=True)
            total = sum(p.stat().st_size for p in csvs)
            provenance = {"source": ", ".join(p.name for p in csvs), "kind": "csv",
                          "bytes": total,
                          # The real thing is ~1.07M rows; a sample is far smaller.
                          "is_real_dataset": len(df) > 900_000}
        elif cfg.allow_sample_fallback:
            raise FileNotFoundError(
                f"No input found in {raw_dir}.\n"
                "Either place online_retail_II.xlsx there (download: "
                "https://archive.ics.uci.edu/dataset/502/online+retail+ii),\n"
                "or run: python tools/generate_sample_retail.py"
            )
        else:
            raise FileNotFoundError(f"No input found in {raw_dir} and fallback is off.")

    df = _coerce_types(df)
    provenance.update({
        "rows": len(df),
        "date_min": df["invoice_date"].min(),
        "date_max": df["invoice_date"].max(),
    })
    return df, provenance


def profile(df: pd.DataFrame) -> pd.DataFrame:
    """Column-level profile printed at ingest - the 'what did we just get' view."""
    rows = []
    for col in df.columns:
        s = df[col]
        rows.append({
            "column": col,
            "dtype": str(s.dtype),
            "nulls": int(s.isna().sum()),
            "null_pct": round(100 * s.isna().mean(), 2),
            "distinct": int(s.nunique(dropna=True)),
            "sample": str(s.dropna().iloc[0])[:28] if s.notna().any() else "",
        })
    return pd.DataFrame(rows)
