"""Unit tests for ingest, cleaning, validation and the star schema.

Run:  pytest -q

These deliberately use a tiny hand-computable dataset. If a KPI is wrong, the
test should tell you the number it expected, not make you reason about 250,000
rows.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.clean import (
    CleansingLog,
    Quarantine,
    build_dim_customer,
    build_dim_product,
    build_star,
    clean_transactions,
)
from src.config import load_config
from src.ingest import SchemaError, normalise_columns
from src.validate import ValidationError, default_rules, run_validation, scorecard


@pytest.fixture
def cfg():
    return load_config(Path(__file__).resolve().parents[1] / "config.yaml")


@pytest.fixture
def raw() -> pd.DataFrame:
    """Eight rows covering every branch the cleaner has to handle."""
    rows = [
        # invoice, stock, desc, qty, date, price, customer, country
        ("536365", "85123A", "WHITE HANGING HEART T-LIGHT HOLDER", 6,
         "2010-12-01 08:26", 2.55, "17850", "United Kingdom"),
        ("536365", "71053", "WHITE METAL LANTERN", 6,
         "2010-12-01 08:26", 3.39, "17850", "United Kingdom"),
        ("536366", "85123A", "WHITE HANGING HEART T-LIGHT HOLDER", 10,
         "2010-12-02 09:01", 2.55, "17851", "France"),
        # a guest checkout - no customer id
        ("536367", "22423", "REGENCY CAKESTAND 3 TIER", 2,
         "2010-12-03 10:15", 12.75, None, "United Kingdom"),
        # a cancellation of the first line
        ("C536368", "85123A", "WHITE HANGING HEART T-LIGHT HOLDER", -6,
         "2010-12-04 11:00", 2.55, "17850", "United Kingdom"),
        # postage - a service line, not a product
        ("536369", "POST", "POSTAGE", 1,
         "2010-12-05 12:00", 18.00, "17851", "France"),
        # warehouse annotation on a zero-price adjustment
        ("536370", "22424", "damaged", 3,
         "2010-12-06 13:00", 0.0, None, "United Kingdom"),
        # negative quantity on a normal invoice - not a valid credit
        ("536371", "22423", "REGENCY CAKESTAND 3 TIER", -4,
         "2010-12-07 14:00", 12.75, "17852", "Germany"),
    ]
    df = pd.DataFrame(rows, columns=[
        "invoice_no", "stock_code", "description", "quantity",
        "invoice_date", "unit_price", "customer_id", "country"])
    df["invoice_date"] = pd.to_datetime(df["invoice_date"])
    df["customer_id"] = df["customer_id"].astype("string")
    for c in ("invoice_no", "stock_code", "description", "country"):
        df[c] = df[c].astype("string")
    return df


@pytest.fixture
def clean(raw, cfg):
    return clean_transactions(raw, cfg, CleansingLog(), Quarantine())


@pytest.fixture
def star(clean):
    return build_star(clean)


# --------------------------------------------------------------------------
# Ingest
# --------------------------------------------------------------------------
def test_column_aliases_online_retail_ii():
    df = pd.DataFrame(columns=["Invoice", "StockCode", "Description", "Quantity",
                               "InvoiceDate", "Price", "Customer ID", "Country"])
    assert list(normalise_columns(df).columns) == [
        "invoice_no", "stock_code", "description", "quantity",
        "invoice_date", "unit_price", "customer_id", "country"]


def test_column_aliases_online_retail_i():
    """The earlier release of the dataset uses different names for 3 columns."""
    df = pd.DataFrame(columns=["InvoiceNo", "StockCode", "Description", "Quantity",
                               "InvoiceDate", "UnitPrice", "CustomerID", "Country"])
    assert list(normalise_columns(df).columns)[:8] == [
        "invoice_no", "stock_code", "description", "quantity",
        "invoice_date", "unit_price", "customer_id", "country"]


def test_missing_column_is_a_clear_error():
    df = pd.DataFrame(columns=["Invoice", "StockCode", "Quantity"])
    with pytest.raises(SchemaError, match="missing required column"):
        normalise_columns(df)


# --------------------------------------------------------------------------
# Cleaning decisions
# --------------------------------------------------------------------------
def test_cancellations_are_kept_not_deleted(clean):
    """Deleting credit notes is the classic error; it overstates revenue."""
    assert clean["is_cancellation"].sum() == 1
    assert (clean.loc[clean["is_cancellation"], "line_revenue"] < 0).all()


def test_service_lines_are_flagged_not_dropped(clean):
    postage = clean[clean["stock_code"] == "POST"]
    assert len(postage) == 1
    assert bool(postage["is_service_line"].iloc[0]) is True
    assert bool(postage["is_product_line"].iloc[0]) is False


def test_warehouse_annotation_is_quarantined(raw, cfg):
    q = Quarantine()
    out = clean_transactions(raw, cfg, CleansingLog(), q)
    assert "22424" not in set(out["stock_code"])
    reasons = set(q.to_frame()["reject_reason"])
    assert any("annotation" in r or "non-positive price" in r for r in reasons)


def test_negative_quantity_without_credit_is_quarantined(raw, cfg):
    q = Quarantine()
    out = clean_transactions(raw, cfg, CleansingLog(), q)
    assert "536371" not in set(out["invoice_no"])
    assert any("negative quantity" in r for r in q.to_frame()["reject_reason"])


def test_guest_checkouts_are_retained(clean):
    assert (~clean["has_customer"]).sum() >= 1
    assert clean.loc[~clean["has_customer"], "line_revenue"].sum() > 0


def test_line_revenue_is_computed_not_trusted(clean):
    expected = (clean["quantity"] * clean["unit_price"]).round(2)
    assert np.allclose(clean["line_revenue"], expected)


def test_exact_duplicates_are_removed(raw, cfg):
    doubled = pd.concat([raw, raw.head(2)], ignore_index=True)
    out = clean_transactions(doubled, cfg, CleansingLog(), Quarantine())
    baseline = clean_transactions(raw, cfg, CleansingLog(), Quarantine())
    assert len(out) == len(baseline)


def test_line_key_is_unique(clean):
    assert clean["invoice_line_key"].is_unique


# --------------------------------------------------------------------------
# Dimensions
# --------------------------------------------------------------------------
def test_product_dimension_has_one_row_per_stock_code(clean):
    dim = build_dim_product(clean)
    assert dim["stock_code"].is_unique
    assert dim["product_key"].is_unique


def test_modal_description_wins(cfg):
    """One stock code, three spellings - the most common one is canonical."""
    rows = []
    for i, desc in enumerate(["RED MUG", "RED MUG", "RED MUGG"]):
        rows.append(("5000" + str(i), "12345", desc, 1,
                     pd.Timestamp("2010-12-01"), 5.0, "17850", "United Kingdom"))
    df = pd.DataFrame(rows, columns=[
        "invoice_no", "stock_code", "description", "quantity",
        "invoice_date", "unit_price", "customer_id", "country"])
    for c in ("invoice_no", "stock_code", "description", "customer_id", "country"):
        df[c] = df[c].astype("string")

    cleaned = clean_transactions(df, cfg, CleansingLog(), Quarantine())
    dim = build_dim_product(cleaned)
    assert dim.loc[dim["stock_code"] == "12345", "description"].iloc[0] == "RED MUG"
    assert dim.loc[dim["stock_code"] == "12345", "description_variants"].iloc[0] == 2


def test_customer_dimension_excludes_guests(clean):
    dim = build_dim_customer(clean)
    assert dim["customer_id"].notna().all()
    assert dim["customer_key"].is_unique
    assert "cohort_month" in dim.columns


def test_date_dimension_is_contiguous(star):
    d = star["dim_date"]
    assert d["date_key"].is_unique
    span = (d["full_date"].max() - d["full_date"].min()).days + 1
    assert len(d) == span


def test_country_dimension_flags_domestic(star):
    c = star["dim_country"]
    assert bool(c.loc[c["country"] == "United Kingdom", "is_domestic"].iloc[0]) is True
    assert set(c["region"]).issubset({"Domestic", "Europe", "Rest of World"})


# --------------------------------------------------------------------------
# Star schema integrity
# --------------------------------------------------------------------------
def test_dimension_join_preserves_fact_grain(clean, star):
    assert len(star["fact_sales"]) == len(clean)


def test_every_fact_row_resolves_its_dimensions(star):
    fact = star["fact_sales"]
    assert fact["product_key"].notna().all()
    assert fact["date_key"].notna().all()
    assert fact["country_key"].notna().all()
    # customer_key is nullable by design - guests have none
    assert fact.loc[fact["has_customer"], "customer_key"].notna().all()


def test_revenue_definitions_are_distinct_and_add_up(star):
    f = star["fact_sales"]
    gross = f.loc[~f["is_cancellation"] & f["is_product_line"], "line_revenue"].sum()
    returns = f.loc[f["is_cancellation"], "line_revenue"].sum()
    service = f.loc[~f["is_cancellation"] & f["is_service_line"], "line_revenue"].sum()

    assert gross == pytest.approx(6 * 2.55 + 6 * 3.39 + 10 * 2.55 + 2 * 12.75)
    assert returns == pytest.approx(-6 * 2.55)
    assert service == pytest.approx(18.00)
    # Net is strictly less than gross whenever a credit note exists.
    assert gross + returns < gross


# --------------------------------------------------------------------------
# Validation engine
# --------------------------------------------------------------------------
def test_clean_data_passes_every_error_rule(star):
    results = run_validation(star, fail_on_error=False)
    errors = results[(results.severity == "ERROR") & (~results.passed)]
    assert errors.empty, errors[["check_name", "failed_rows"]].to_string()


def test_negative_price_aborts_the_load(star):
    star["fact_sales"].loc[0, "unit_price"] = -1.0
    with pytest.raises(ValidationError, match="aborted"):
        run_validation(star, fail_on_error=True)


def test_revenue_inconsistency_is_caught(star):
    star["fact_sales"].loc[0, "line_revenue"] = 999.0
    results = run_validation(star, fail_on_error=False)
    row = results[results.check_name == "Line revenue equals quantity x price"].iloc[0]
    assert row["failed_rows"] == 1


def test_orphan_product_key_is_caught(star):
    star["fact_sales"].loc[0, "product_key"] = 999999
    results = run_validation(star, fail_on_error=False)
    row = results[results.check_name == "Sales resolve to a product"].iloc[0]
    assert row["failed_rows"] == 1 and not row["passed"]


def test_duplicate_line_key_is_caught(star):
    f = star["fact_sales"]
    star["fact_sales"] = pd.concat([f, f.head(1)], ignore_index=True)
    results = run_validation(star, fail_on_error=False)
    row = results[results.check_name == "Invoice line key is unique"].iloc[0]
    assert row["failed_rows"] == 1


def test_positive_revenue_on_a_cancelled_product_line_is_caught(star):
    """A credit note that adds product value is incoherent and must fail."""
    f = star["fact_sales"]
    idx = f.index[f["is_cancellation"] & f["is_product_line"]][0]
    f.loc[idx, "line_revenue"] = 50.0
    results = run_validation(star, fail_on_error=False)
    row = results[
        results.check_name == "Cancelled product lines carry negative revenue"].iloc[0]
    assert row["failed_rows"] == 1


def test_service_adjustment_on_a_credit_note_is_allowed(star):
    """The real dataset posts a MANUAL adjustment of +GBP 373.57 onto credit
    invoice C496350. That is ordinary bookkeeping, not a corrupt row - the
    ERROR rule must not fire on it, though the WARN rule should notice."""
    f = star["fact_sales"].copy()
    row = f[f["is_cancellation"]].iloc[0].copy()
    row["invoice_line_key"] = "C999999-1"
    row["is_product_line"] = False
    row["is_service_line"] = True
    row["line_revenue"] = 373.57
    row["quantity"] = 1
    row["unit_price"] = 373.57
    star["fact_sales"] = pd.concat([f, row.to_frame().T], ignore_index=True)

    results = run_validation(star, fail_on_error=False)
    error_rule = results[
        results.check_name == "Cancelled product lines carry negative revenue"].iloc[0]
    warn_rule = results[
        results.check_name == "Credit notes contain no positive product value"].iloc[0]

    assert error_rule["failed_rows"] == 0, "service lines must be exempt"
    assert warn_rule["failed_rows"] == 1, "but it should still be surfaced"


def test_missing_table_does_not_crash_the_suite(star):
    del star["dim_country"]
    results = run_validation(star, fail_on_error=False)
    assert "not present" in " ".join(results["details"].fillna(""))


def test_every_rule_targets_a_table_the_star_provides():
    known = {"fact_sales", "dim_product", "dim_customer", "dim_date", "dim_country"}
    assert {r.table for r in default_rules()}.issubset(known)


def test_scorecard_shape():
    df = pd.DataFrame({
        "check_category": ["Validity", "Validity", "Uniqueness"],
        "check_name": ["a", "b", "c"],
        "passed": [True, False, True],
        "failed_rows": [0, 5, 0],
    })
    sc = scorecard(df)
    assert sc[sc.check_category == "Validity"]["pass_rate_pct"].iloc[0] == 50.0
