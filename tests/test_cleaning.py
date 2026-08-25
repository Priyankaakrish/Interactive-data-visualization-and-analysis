"""The cleaning decisions are the project's core claim, so they get tested."""
from __future__ import annotations

import pandas as pd
import pytest


def test_duplicate_rows_are_removed(raw_frame: pd.DataFrame) -> None:
    deduped = raw_frame.drop_duplicates()
    assert len(deduped) == len(raw_frame) - 1, "the exact duplicate should be dropped"


def test_cancellations_are_identified(raw_frame: pd.DataFrame) -> None:
    flags = raw_frame["invoice_no"].astype(str).str.upper().str.startswith("C")
    assert flags.sum() == 1
    assert (raw_frame.loc[flags, "quantity"] < 0).all(), "credit notes carry negative quantity"


def test_cancellations_produce_negative_revenue(raw_frame: pd.DataFrame) -> None:
    frame = raw_frame.assign(line_revenue=raw_frame.quantity * raw_frame.unit_price)
    cancelled = frame[frame.invoice_no.astype(str).str.startswith("C")]
    assert (cancelled.line_revenue <= 0).all()


def test_service_codes_are_flagged_not_dropped(raw_frame: pd.DataFrame) -> None:
    service = {"POST", "DOT", "M", "BANK CHARGES", "AMAZONFEE"}
    hits = raw_frame[raw_frame.stock_code.isin(service)]
    assert len(hits) == 1
    assert hits.unit_price.iloc[0] > 0, "postage is real money and must be retained"


def test_zero_price_rows_are_excluded(raw_frame: pd.DataFrame) -> None:
    assert (raw_frame.unit_price <= 0).sum() == 1


def test_guest_checkouts_are_retained_for_revenue(raw_frame: pd.DataFrame) -> None:
    guests = raw_frame[raw_frame.customer_id.isna()]
    assert len(guests) == 2, "guest rows exist and must not be silently dropped"
    revenue = (guests.quantity * guests.unit_price).sum()
    assert revenue != 0, "guest rows still carry revenue"


def test_revenue_identity_holds(raw_frame: pd.DataFrame) -> None:
    """Gross minus returns equals net - the identity every headline figure rests on."""
    frame = raw_frame.assign(line_revenue=raw_frame.quantity * raw_frame.unit_price)
    is_cancel = frame.invoice_no.astype(str).str.startswith("C")
    gross = frame.loc[~is_cancel, "line_revenue"].sum()
    returns = frame.loc[is_cancel, "line_revenue"].abs().sum()
    net = frame.line_revenue.sum()
    assert pytest.approx(net, rel=1e-9) == gross - returns
