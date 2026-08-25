"""Cleaning - stage 2, and the stage that decides whether the numbers mean anything.

Online Retail II is a genuinely messy commercial extract. The defects are not
random noise; each one is a business event that the source system recorded
badly, and each needs a *decision* rather than a blanket drop:

* Invoices prefixed 'C' are cancellations. They carry negative quantities and
  are real revenue reversals - they must be kept and netted off, not deleted.
  Deleting them is the most common error made with this dataset and inflates
  revenue by roughly 2%.
* Some StockCodes are charges, not products: POST, DOT, M, BANK CHARGES,
  AMAZONFEE, gift vouchers. They are real money, so they stay in the fact
  table, but they are flagged so product analysis can exclude them.
* Some Descriptions are warehouse annotations ("damaged", "check", "?") on
  zero-price stock adjustments. Those are not sales and are quarantined.
* Roughly a fifth of rows have no Customer ID - guest checkouts. They count
  toward revenue but cannot participate in RFM or cohort analysis.
* One StockCode can carry several spellings of its Description. The modal
  description per code is promoted to canonical so the product dimension has
  exactly one row per product.

Every decision is written to a cleansing log, and every rejected row is
quarantined with a reason rather than silently dropped.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

DEFAULT_SERVICE_CODES = [
    "POST", "DOT", "C2", "M", "S", "D", "B", "CRUK", "PADS",
    "BANK CHARGES", "AMAZONFEE", "ADJUST", "ADJUST2", "TEST001", "TEST002",
]


@dataclass
class CleansingLog:
    """Append-only audit trail of what cleaning did and why."""

    entries: list[dict] = field(default_factory=list)

    def add(self, step: str, rows: int, decision: str, note: str = "") -> None:
        self.entries.append({"Step": step, "RowsAffected": int(rows),
                             "Decision": decision, "Note": note})
        if rows:
            log.info("%s -> %s rows (%s)", step, rows, decision)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.entries,
                            columns=["Step", "RowsAffected", "Decision", "Note"])


class Quarantine:
    """Rows removed from analysis, retained with a reason for the data steward."""

    def __init__(self) -> None:
        self.frames: list[pd.DataFrame] = []

    def add(self, df: pd.DataFrame, reason: str) -> None:
        if len(df):
            self.frames.append(df.assign(reject_reason=reason))

    def to_frame(self) -> pd.DataFrame:
        if not self.frames:
            return pd.DataFrame()
        return pd.concat(self.frames, ignore_index=True)


# --------------------------------------------------------------------------
def clean_transactions(df: pd.DataFrame, cfg,
                       clog: CleansingLog | None = None,
                       quarantine: Quarantine | None = None) -> pd.DataFrame:
    """Return the analysis-ready transaction fact."""
    clog = clog or CleansingLog()
    quarantine = quarantine if quarantine is not None else Quarantine()
    settings = cfg.cleaning
    validation = cfg.validation

    service_codes = {c.upper() for c in settings.get("service_codes",
                                                     DEFAULT_SERVICE_CODES)}
    junk_patterns = settings.get("junk_description_patterns", [])
    price_lo, price_hi = validation.get("price_bounds", [0.001, 10000.0])

    start_rows = len(df)
    df = df.copy()

    # -- 1. Text normalisation --------------------------------------------
    for col in ("invoice_no", "stock_code", "country"):
        df[col] = df[col].astype("string").str.strip()
    df["stock_code"] = df["stock_code"].str.upper()
    df["description"] = (df["description"].astype("string")
                                          .str.strip()
                                          .str.replace(r"\s+", " ", regex=True)
                                          .str.upper())
    clog.add("normalise text (trim, collapse spaces, uppercase)", len(df), "modified")

    # -- 2. Exact duplicates ----------------------------------------------
    dupe_cols = ["invoice_no", "stock_code", "quantity", "invoice_date",
                 "unit_price", "customer_id"]
    dupes = df.duplicated(subset=dupe_cols, keep="first")
    quarantine.add(df.loc[dupes], "exact duplicate row")
    clog.add("remove exact duplicate rows", int(dupes.sum()), "quarantined",
             "a replayed load double-counts revenue")
    df = df.loc[~dupes].copy()

    # -- 3. Unparseable rows ----------------------------------------------
    unusable = df["invoice_date"].isna() | df["quantity"].isna() | df["unit_price"].isna()
    quarantine.add(df.loc[unusable], "missing date, quantity or price")
    clog.add("remove rows with unparseable date/quantity/price",
             int(unusable.sum()), "quarantined")
    df = df.loc[~unusable].copy()

    # -- 4. Classify the row before judging it ----------------------------
    df["is_cancellation"] = (df["invoice_no"].str.upper().str.startswith("C")
                             .fillna(False))
    df["is_service_line"] = (
        df["stock_code"].isin(service_codes)
        | df["stock_code"].str.startswith("GIFT_", na=False)
        | df["stock_code"].str.fullmatch(r"[A-Z ]{1,12}", na=False)
    )
    df["is_product_line"] = ~df["is_service_line"]
    clog.add("flag cancellations", int(df["is_cancellation"].sum()), "flagged",
             "kept and netted off revenue, never deleted")
    clog.add("flag non-product service lines", int(df["is_service_line"].sum()),
             "flagged", "postage, fees, vouchers - real money, not products")

    # -- 5. Warehouse annotations -----------------------------------------
    if junk_patterns:
        pattern = "|".join(junk_patterns)
        junk = (df["description"].fillna("").str.contains(pattern, case=False,
                                                          regex=True, na=False)
                & df["is_product_line"])
        quarantine.add(df.loc[junk], "warehouse annotation, not a sale")
        clog.add("remove warehouse annotations in Description",
                 int(junk.sum()), "quarantined",
                 "'damaged', 'check', '?' are stock adjustments")
        df = df.loc[~junk].copy()

    # -- 6. Price validity -------------------------------------------------
    zero_price = (df["unit_price"] <= 0) & df["is_product_line"]
    quarantine.add(df.loc[zero_price], "non-positive price on a product line")
    clog.add("remove non-positive prices", int(zero_price.sum()), "quarantined",
             "giveaways and adjustments carry no revenue signal")
    df = df.loc[~zero_price].copy()

    implausible = (df["unit_price"] > price_hi) & df["is_product_line"]
    quarantine.add(df.loc[implausible], f"price above {price_hi}")
    clog.add(f"remove implausible prices (> {price_hi})", int(implausible.sum()),
             "quarantined", "decimal-point slips distort every average")
    df = df.loc[~implausible].copy()

    # -- 7. Quantity coherence --------------------------------------------
    # A negative quantity is only legitimate on a cancellation.
    bad_negative = (df["quantity"] < 0) & ~df["is_cancellation"]
    quarantine.add(df.loc[bad_negative], "negative quantity without a credit invoice")
    clog.add("remove negative quantity outside a credit note",
             int(bad_negative.sum()), "quarantined")
    df = df.loc[~bad_negative].copy()

    zero_qty = df["quantity"] == 0
    quarantine.add(df.loc[zero_qty], "zero quantity")
    clog.add("remove zero-quantity lines", int(zero_qty.sum()), "quarantined")
    df = df.loc[~zero_qty].copy()

    max_qty = validation.get("max_line_quantity", 80000)
    extreme = df["quantity"].abs() > max_qty
    quarantine.add(df.loc[extreme], f"quantity beyond {max_qty}")
    clog.add(f"remove extreme quantities (> {max_qty})", int(extreme.sum()),
             "quarantined")
    df = df.loc[~extreme].copy()

    # -- 7b. Credit-note coherence ----------------------------------------
    # A cancellation that adds product value is incoherent. Service lines are
    # exempt: a manual adjustment or a postage refund posted on a credit note
    # is ordinary bookkeeping.
    incoherent_credit = (df["is_cancellation"] & df["is_product_line"]
                         & (df["quantity"] > 0))
    quarantine.add(df.loc[incoherent_credit],
                   "credit note with positive product quantity")
    clog.add("quarantine incoherent credit-note product lines",
             int(incoherent_credit.sum()), "quarantined")
    df = df.loc[~incoherent_credit].copy()

    # -- 8. Customer identity ---------------------------------------------
    df["has_customer"] = df["customer_id"].notna()
    n_guest = int((~df["has_customer"]).sum())
    if settings.get("keep_missing_customer", True):
        clog.add("guest checkouts without a Customer ID", n_guest, "kept",
                 "counted in revenue, excluded from RFM and cohort analysis")
    else:
        quarantine.add(df.loc[~df["has_customer"]], "missing customer id")
        clog.add("guest checkouts without a Customer ID", n_guest, "quarantined")
        df = df.loc[df["has_customer"]].copy()

    # -- 9. Derived measures ----------------------------------------------
    df["line_revenue"] = (df["quantity"] * df["unit_price"]).round(2)
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    df["invoice_day"] = df["invoice_date"].dt.normalize()
    df["date_key"] = df["invoice_date"].dt.strftime("%Y%m%d").astype(int)
    df["year_month"] = df["invoice_date"].dt.strftime("%Y-%m")
    # A stable line identifier - the source has no primary key of its own.
    df["line_no"] = df.groupby("invoice_no").cumcount() + 1
    df["invoice_line_key"] = df["invoice_no"].astype(str) + "-" + df["line_no"].astype(str)
    clog.add("derive line_revenue, date keys and a line primary key", len(df),
             "added", "the source ships no primary key")

    clog.add("TOTAL", start_rows - len(df), "removed",
             f"{len(df):,} of {start_rows:,} rows retained "
             f"({len(df) / start_rows:.1%})")
    return df


# --------------------------------------------------------------------------
def build_dim_product(df: pd.DataFrame) -> pd.DataFrame:
    """One row per stock code, with the modal description promoted to canonical.

    The same code often appears with several spellings. Picking the most
    frequent one is the only choice that keeps the dimension at one row per
    product without inventing a name.
    """
    grp = df.groupby("stock_code")
    canonical = (
        grp["description"]
        .agg(lambda s: s.dropna().mode().iloc[0] if s.notna().any() else "(UNKNOWN)")
        .rename("description")
    )
    stats = grp.agg(
        description_variants=("description", "nunique"),
        first_sold=("invoice_day", "min"),
        last_sold=("invoice_day", "max"),
        avg_unit_price=("unit_price", "mean"),
        is_service_line=("is_service_line", "max"),
    )
    out = pd.concat([canonical, stats], axis=1).reset_index()
    out["avg_unit_price"] = out["avg_unit_price"].round(2)
    out["is_product"] = ~out["is_service_line"].astype(bool)
    out["product_key"] = np.arange(1, len(out) + 1)
    return out[["product_key", "stock_code", "description", "description_variants",
                "avg_unit_price", "first_sold", "last_sold",
                "is_service_line", "is_product"]]


def build_dim_customer(df: pd.DataFrame) -> pd.DataFrame:
    """One row per identified customer, with their cohort and home country."""
    known = df[df["has_customer"]].copy()
    if known.empty:
        return pd.DataFrame(columns=["customer_key", "customer_id", "country"])

    grp = known.groupby("customer_id")
    out = grp.agg(
        # A handful of customers order from more than one country; the modal
        # country is their home market.
        country=("country", lambda s: s.mode().iloc[0]),
        first_purchase=("invoice_day", "min"),
        last_purchase=("invoice_day", "max"),
        countries_seen=("country", "nunique"),
    ).reset_index()
    out["cohort_month"] = out["first_purchase"].dt.strftime("%Y-%m")
    out["customer_key"] = np.arange(1, len(out) + 1)
    return out[["customer_key", "customer_id", "country", "cohort_month",
                "first_purchase", "last_purchase", "countries_seen"]]


def build_dim_country(df: pd.DataFrame) -> pd.DataFrame:
    """Country dimension with a region rollup and a domestic-market flag."""
    europe = {
        "United Kingdom", "EIRE", "Germany", "France", "Netherlands", "Spain",
        "Belgium", "Switzerland", "Portugal", "Italy", "Sweden", "Norway",
        "Finland", "Austria", "Denmark", "Poland", "Czech Republic", "Greece",
        "Iceland", "Malta", "Cyprus", "Lithuania", "Channel Islands",
        "European Community", "Unspecified",
    }
    codes = sorted(df["country"].dropna().unique())
    rows = []
    for i, c in enumerate(codes, start=1):
        rows.append({
            "country_key": i,
            "country": c,
            "region": ("Domestic" if c == "United Kingdom"
                       else "Europe" if c in europe else "Rest of World"),
            "is_domestic": c == "United Kingdom",
        })
    return pd.DataFrame(rows)


def build_dim_date(df: pd.DataFrame) -> pd.DataFrame:
    """Contiguous date dimension spanning the transaction window."""
    lo, hi = df["invoice_day"].min(), df["invoice_day"].max()
    d = pd.date_range(lo, hi, freq="D")
    out = pd.DataFrame({"full_date": d})
    out["date_key"] = out["full_date"].dt.strftime("%Y%m%d").astype(int)
    out["year"] = out["full_date"].dt.year
    out["quarter"] = out["full_date"].dt.quarter
    out["month_number"] = out["full_date"].dt.month
    out["month_name"] = out["full_date"].dt.strftime("%B")
    out["year_month"] = out["full_date"].dt.strftime("%Y-%m")
    out["month_start"] = out["full_date"].values.astype("datetime64[M]")
    out["day_of_week"] = out["full_date"].dt.dayofweek + 1
    out["day_name"] = out["full_date"].dt.strftime("%A")
    out["week_of_year"] = out["full_date"].dt.isocalendar().week.astype(int)
    out["is_weekend"] = out["day_of_week"].isin([6, 7])
    return out[["date_key", "full_date", "year", "quarter", "month_number",
                "month_name", "year_month", "month_start", "day_of_week",
                "day_name", "week_of_year", "is_weekend"]]


def build_fact_sales(df: pd.DataFrame, dim_product: pd.DataFrame,
                     dim_customer: pd.DataFrame,
                     dim_country: pd.DataFrame) -> pd.DataFrame:
    """Attach surrogate keys and emit the fact at invoice-line grain."""
    fact = (
        df.merge(dim_product[["product_key", "stock_code"]], on="stock_code", how="left")
          .merge(dim_customer[["customer_key", "customer_id"]], on="customer_id",
                 how="left")
          .merge(dim_country[["country_key", "country"]], on="country", how="left")
    )
    if len(fact) != len(df):
        raise AssertionError(
            f"Dimension join changed the fact grain: {len(df)} -> {len(fact)}. "
            "A dimension has duplicate keys."
        )
    return fact[[
        "invoice_line_key", "invoice_no", "line_no", "date_key", "invoice_date",
        "product_key", "customer_key", "country_key", "stock_code",
        "quantity", "unit_price", "line_revenue",
        "is_cancellation", "is_service_line", "is_product_line", "has_customer",
    ]]


def build_star(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Assemble the full star schema from the cleaned transactions."""
    dim_product = build_dim_product(df)
    dim_customer = build_dim_customer(df)
    dim_country = build_dim_country(df)
    dim_date = build_dim_date(df)
    fact = build_fact_sales(df, dim_product, dim_customer, dim_country)
    return {
        "dim_date": dim_date,
        "dim_product": dim_product,
        "dim_customer": dim_customer,
        "dim_country": dim_country,
        "fact_sales": fact,
    }
