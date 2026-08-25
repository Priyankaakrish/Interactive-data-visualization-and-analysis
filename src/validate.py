"""Validation - the gate in front of PostgreSQL.

A rule is a predicate returning a boolean Series where True marks a FAILING
row. Rules carry a category and a severity; ERROR-severity failures raise and
abort the run, so nothing that breaks a stated invariant ever reaches the
database. WARN rules are recorded and carried forward, because "suspicious"
and "wrong" deserve different responses.

Because this runs before the load rather than after it, the database only ever
contains data that has passed. The trade-off is that PostgreSQL never sees the
raw defects, so the results are persisted into monitoring.dq_result - otherwise
the evidence of what was caught would live only in a console log.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import numpy as np
import pandas as pd

Severity = str  # "ERROR" | "WARN" | "INFO"


@dataclass(frozen=True)
class Rule:
    name: str
    table: str
    category: str      # Completeness | Validity | Uniqueness | Referential | Consistency
    severity: Severity
    predicate: Callable[[pd.DataFrame], pd.Series]
    details: str = ""


class ValidationError(RuntimeError):
    """Raised when an ERROR-severity rule fails and fail_on_error is set."""


# --------------------------------------------------------------------------
# Predicate factories
# --------------------------------------------------------------------------
def _none(df: pd.DataFrame) -> pd.Series:
    return pd.Series(False, index=df.index)


def is_null(column: str):
    return lambda df: df[column].isna() if column in df else _none(df)


def not_positive(column: str):
    def _p(df):
        if column not in df:
            return _none(df)
        return (pd.to_numeric(df[column], errors="coerce") <= 0).fillna(False)
    return _p


def outside_range(column: str, low: float, high: float):
    def _p(df):
        if column not in df:
            return _none(df)
        s = pd.to_numeric(df[column], errors="coerce")
        return ((s < low) | (s > high)).fillna(False)
    return _p


def duplicated_on(*columns: str):
    def _p(df):
        cols = [c for c in columns if c in df]
        return df.duplicated(subset=cols, keep="first") if cols else _none(df)
    return _p


def orphan_key(column: str, parent: str, parent_key: str):
    def _p(df):
        tables = getattr(_p, "tables", {}) or {}
        if column not in df or parent not in tables:
            return _none(df)
        valid = set(tables[parent][parent_key].dropna().unique())
        return df[column].notna() & ~df[column].isin(valid)
    _p.tables = {}
    return _p


def iqr_outlier(column: str, multiplier: float = 3.0):
    def _p(df):
        if column not in df:
            return _none(df)
        s = pd.to_numeric(df[column], errors="coerce")
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if not np.isfinite(iqr) or iqr == 0:
            return _none(df)
        return ((s < q1 - multiplier * iqr) | (s > q3 + multiplier * iqr)).fillna(False)
    return _p


def inconsistent_sign(qty_col: str, flag_col: str):
    """A negative quantity is only valid on a cancellation, and vice versa."""
    def _p(df):
        if qty_col not in df or flag_col not in df:
            return _none(df)
        neg = pd.to_numeric(df[qty_col], errors="coerce") < 0
        return (neg & ~df[flag_col].astype(bool)).fillna(False)
    return _p


# --------------------------------------------------------------------------
# Rule set
# --------------------------------------------------------------------------
def default_rules(price_bounds: tuple[float, float] = (0.001, 10000.0),
                  iqr_mult: float = 3.0,
                  max_qty: int = 80000) -> list[Rule]:
    lo, hi = price_bounds
    return [
        # -- Completeness --------------------------------------------------
        Rule("Invoice number is present", "fact_sales", "Completeness", "ERROR",
             is_null("invoice_no")),
        Rule("Stock code is present", "fact_sales", "Completeness", "ERROR",
             is_null("stock_code")),
        Rule("Invoice date is present", "fact_sales", "Completeness", "ERROR",
             is_null("invoice_date"), "A null date drops the row from every trend."),
        Rule("Line revenue is present", "fact_sales", "Completeness", "ERROR",
             is_null("line_revenue")),
        Rule("Product has a description", "dim_product", "Completeness", "WARN",
             lambda df: df["description"].isna() | (df["description"] == "(UNKNOWN)"),
             "Unnamed products still sell; they just cannot be labelled."),
        Rule("Customer is identified", "fact_sales", "Completeness", "WARN",
             lambda df: ~df["has_customer"].astype(bool),
             "Guest checkouts are expected at roughly 20% and are not an error."),

        # -- Validity ------------------------------------------------------
        Rule("Unit price is positive on product lines", "fact_sales", "Validity",
             "ERROR",
             lambda df: (df["unit_price"] <= 0) & df["is_product_line"].astype(bool)),
        Rule("Unit price within plausible bounds", "fact_sales", "Validity", "ERROR",
             lambda df: outside_range("unit_price", lo, hi)(df)
                        & df["is_product_line"].astype(bool),
             f"Outside {lo}-{hi} indicates a decimal-point slip."),
        Rule("Negative quantity only on cancellations", "fact_sales", "Validity",
             "ERROR", inconsistent_sign("quantity", "is_cancellation")),
        Rule("Quantity is non-zero", "fact_sales", "Validity", "ERROR",
             lambda df: (df["quantity"] == 0).fillna(False)),
        Rule("Quantity within operational limit", "fact_sales", "Validity", "ERROR",
             lambda df: (df["quantity"].abs() > max_qty).fillna(False)),
        Rule("Line revenue equals quantity x price", "fact_sales", "Consistency",
             "ERROR",
             lambda df: ((df["line_revenue"]
                          - (df["quantity"] * df["unit_price"])).abs() > 0.01).fillna(False),
             "Guards against a derived measure drifting from its inputs."),
        # A credit note must reverse value - but only on product lines. The real
        # dataset contains a manual adjustment (stock code M) posted onto credit
        # invoice C496350 at +GBP 373.57, which is a legitimate accounting entry,
        # not a corrupt row. Binding the invariant to product lines keeps it
        # strict where it matters without failing the load on correct data.
        Rule("Cancelled product lines carry negative revenue", "fact_sales",
             "Consistency", "ERROR",
             lambda df: (df["is_cancellation"].astype(bool)
                         & df["is_product_line"].astype(bool)
                         & (df["line_revenue"] > 0)).fillna(False)),
        Rule("Credit notes contain no positive product value", "fact_sales",
             "Consistency", "WARN",
             lambda df: (df["is_cancellation"].astype(bool)
                         & (df["line_revenue"] > 0)).fillna(False),
             "Service adjustments on a credit note are expected; product lines are not."),
        Rule("Line revenue has no extreme outliers", "fact_sales", "Validity", "WARN",
             iqr_outlier("line_revenue", iqr_mult),
             "Wholesale orders are legitimately large; flagged, not removed."),

        # -- Uniqueness ----------------------------------------------------
        Rule("Invoice line key is unique", "fact_sales", "Uniqueness", "ERROR",
             duplicated_on("invoice_line_key"),
             "Duplicate lines double-count revenue."),
        Rule("Product key is unique", "dim_product", "Uniqueness", "ERROR",
             duplicated_on("product_key")),
        Rule("Stock code appears once in the dimension", "dim_product", "Uniqueness",
             "ERROR", duplicated_on("stock_code")),
        Rule("Customer key is unique", "dim_customer", "Uniqueness", "ERROR",
             duplicated_on("customer_key")),
        Rule("Date key is unique", "dim_date", "Uniqueness", "ERROR",
             duplicated_on("date_key")),
        Rule("Country key is unique", "dim_country", "Uniqueness", "ERROR",
             duplicated_on("country_key")),

        # -- Referential ---------------------------------------------------
        Rule("Sales resolve to a product", "fact_sales", "Referential", "ERROR",
             orphan_key("product_key", "dim_product", "product_key")),
        Rule("Sales resolve to a date", "fact_sales", "Referential", "ERROR",
             orphan_key("date_key", "dim_date", "date_key")),
        Rule("Sales resolve to a country", "fact_sales", "Referential", "ERROR",
             orphan_key("country_key", "dim_country", "country_key")),
        Rule("Identified sales resolve to a customer", "fact_sales", "Referential",
             "ERROR", orphan_key("customer_key", "dim_customer", "customer_key")),
    ]


# --------------------------------------------------------------------------
def run_validation(tables: dict[str, pd.DataFrame],
                   rules: list[Rule] | None = None,
                   fail_on_error: bool = True,
                   price_bounds: tuple[float, float] = (0.001, 10000.0),
                   iqr_multiplier: float = 3.0,
                   max_quantity: int = 80000,
                   run_id: str | None = None) -> pd.DataFrame:
    """Execute every rule; return a tidy result frame."""
    rules = rules or default_rules(price_bounds, iqr_multiplier, max_quantity)
    ts = datetime.now(timezone.utc).replace(microsecond=0)
    rows = []

    for rule in rules:
        df = tables.get(rule.table)
        if df is None:
            rows.append(_row(ts, run_id, rule, -1, 0, "table not present"))
            continue

        predicate = rule.predicate
        if hasattr(predicate, "tables"):
            predicate.tables = tables

        try:
            mask = predicate(df)
            failed = int(pd.Series(mask).astype(bool).sum())
            note = rule.details
        except Exception as exc:              # a broken rule is itself a finding
            failed, note = -1, f"rule raised {type(exc).__name__}: {exc}"

        rows.append(_row(ts, run_id, rule, failed, len(df), note))

    result = pd.DataFrame(rows)

    if fail_on_error:
        breaches = result[(result["severity"] == "ERROR") & (~result["passed"])]
        if not breaches.empty:
            names = "; ".join(
                f"{r.check_name} ({r.failed_rows} rows)" for r in breaches.itertuples()
            )
            raise ValidationError(
                f"{len(breaches)} ERROR-severity check(s) failed, load aborted: {names}"
            )
    return result


def _row(ts, run_id, rule: Rule, failed: int, total: int, note: str) -> dict:
    return {
        "run_ts": ts,
        "run_id": run_id,
        "check_name": rule.name,
        "check_category": rule.category,
        "target_table": rule.table,
        "severity": rule.severity,
        "failed_rows": failed,
        "total_rows": total,
        "fail_rate_pct": round(100.0 * failed / total, 4) if total and failed > 0 else 0.0,
        "passed": failed == 0,
        "details": note,
    }


def scorecard(results: pd.DataFrame) -> pd.DataFrame:
    g = results.groupby("check_category", as_index=False).agg(
        checks=("check_name", "count"),
        passed=("passed", "sum"),
        failed_rows=("failed_rows", "sum"),
    )
    g["pass_rate_pct"] = (100.0 * g["passed"] / g["checks"]).round(1)
    return g.sort_values("pass_rate_pct")
