"""Generate a sample with the exact Online Retail II schema and defect profile.

The real dataset is a 43.5 MB Excel workbook that requires a manual download:
https://archive.ics.uci.edu/dataset/502/online+retail+ii

This script writes a CSV with identical columns and, more importantly, the same
*kinds* of mess: C-prefix cancellations, service stock codes that are not
products, warehouse annotations in the Description field, missing Customer IDs
on guest checkouts, exact duplicate rows, zero-price adjustments and wholesale
quantity spikes. The cleaning and validation layers therefore get a real
workout on a fresh clone, and swapping in the genuine workbook changes nothing
but the row count.

Usage:  python tools/generate_sample_retail.py [--rows 250000] [--seed 42]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "raw"

# The real file is overwhelmingly UK; any analysis that misses that is wrong.
COUNTRIES = [
    ("United Kingdom", 0.9130), ("EIRE", 0.0180), ("Germany", 0.0170),
    ("France", 0.0150), ("Netherlands", 0.0055), ("Spain", 0.0045),
    ("Belgium", 0.0040), ("Switzerland", 0.0035), ("Portugal", 0.0030),
    ("Australia", 0.0028), ("Italy", 0.0025), ("Sweden", 0.0022),
    ("Channel Islands", 0.0020), ("Norway", 0.0018), ("Finland", 0.0015),
    ("Austria", 0.0012), ("Denmark", 0.0010), ("Japan", 0.0008),
    ("Unspecified", 0.0004), ("European Community", 0.0003),
]

# Gift-ware vocabulary - the real descriptions are ALL CAPS noun phrases.
ADJ = ["VINTAGE", "RETROSPOT", "REGENCY", "ANTIQUE", "RUSTIC", "FRENCH",
       "SCANDINAVIAN", "VICTORIAN", "PAISLEY", "POLKADOT", "GINGHAM",
       "WOODLAND", "SEASIDE", "CHRISTMAS", "HEART", "STAR", "SPOTTY"]
COLOUR = ["RED", "WHITE", "BLUE", "PINK", "GREEN", "CREAM", "IVORY",
          "BLACK", "SILVER", "GOLD", "LILAC", "TURQUOISE"]
NOUN = ["T-LIGHT HOLDER", "CAKESTAND 3 TIER", "JUMBO BAG", "LUNCH BAG",
        "PAPER NAPKINS", "CERAMIC JAR", "WOODEN FRAME", "GLASS CLOCHE",
        "STORAGE TIN", "TEA TOWEL", "BUNTING", "DOORMAT", "CUSHION COVER",
        "MUG", "TEACUP AND SAUCER", "CAKE CASES", "PARTY BAGS", "GARLAND",
        "PHOTO FRAME", "JEWELLERY BOX", "CANDLE HOLDER", "BIRD ORNAMENT",
        "HAND WARMER", "WATER BOTTLE", "APRON", "PLACEMAT", "COASTER SET",
        "LANTERN", "MONEY BANK", "DOORSTOP", "NOTEBOOK", "GIFT WRAP"]

# Charges and adjustments, not sellable products. Present in the real file.
SERVICE = [
    ("POST", "POSTAGE", 18.00), ("DOT", "DOTCOM POSTAGE", 569.77),
    ("C2", "CARRIAGE", 50.00), ("M", "Manual", 1.25),
    ("BANK CHARGES", "Bank Charges", 15.00), ("AMAZONFEE", "AMAZON FEE", 13541.33),
    ("CRUK", "CRUK Commission", 65.00), ("D", "Discount", 5.00),
    ("S", "SAMPLES", 2.50), ("PADS", "PADS TO MATCH ALL CUSHIONS", 0.001),
]

# Warehouse annotations that appear in Description instead of a product name.
JUNK_DESCRIPTIONS = [
    "?", "??", "check", "damaged", "damages", "found", "lost", "missing",
    "smashed", "thrown away", "mouldy", "water damaged", "wrongly sold",
    "sold as set on dotcom", "adjustment", "wet damaged", "crushed boxes",
    "faulty", "unsaleable, destroyed", "incorrectly credited",
]


def build_catalogue(rng, n_products: int) -> pd.DataFrame:
    """Stock codes with a canonical description and a base price."""
    codes, descs, prices = [], [], []
    seen = set()
    while len(codes) < n_products:
        code = str(rng.integers(10000, 99999))
        if code in seen:
            continue
        seen.add(code)
        desc = f"{rng.choice(ADJ)} {rng.choice(COLOUR)} {rng.choice(NOUN)}"
        # Gift-ware is cheap; a long right tail covers the occasional big item.
        price = round(float(np.clip(rng.lognormal(0.75, 0.85), 0.19, 295.0)), 2)
        codes.append(code)
        descs.append(desc)
        prices.append(price)

    df = pd.DataFrame({"stock_code": codes, "description": descs, "base_price": prices})
    # Real stock codes often carry a letter suffix for a colourway variant.
    variant = rng.random(len(df)) < 0.18
    df.loc[variant, "stock_code"] = (
        df.loc[variant, "stock_code"] + rng.choice(list("ABCDEFGHJPS"), variant.sum())
    )
    df["popularity"] = rng.pareto(1.3, len(df)) + 0.03
    df["popularity"] /= df["popularity"].sum()
    return df


def build_customers(rng, n: int) -> pd.DataFrame:
    names = [c[0] for c in COUNTRIES]
    p = np.array([c[1] for c in COUNTRIES], dtype=float)
    p /= p.sum()
    # Country is a property of the customer, not the order.
    return pd.DataFrame({
        "customer_id": np.arange(12346, 12346 + n),
        "country": rng.choice(names, size=n, p=p),
        # A minority of wholesale accounts drive most of the volume.
        "weight": rng.pareto(1.1, n) + 0.05,
    })


def build_transactions(rng, n_rows: int, catalogue: pd.DataFrame,
                       customers: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="D")
    dow = dates.dayofweek.to_numpy()
    month = dates.month.to_numpy()
    # Retail gift-ware peaks Sep-Nov as wholesalers stock up for Christmas,
    # then collapses in the new year.
    seasonal = np.select(
        [np.isin(month, [9, 10, 11]), np.isin(month, [12]), np.isin(month, [1, 2])],
        [2.15, 1.35, 0.55], default=1.0,
    )
    # The business is closed Saturdays in this dataset.
    weekday = np.where(dow == 5, 0.0, np.where(dow == 6, 0.55, 1.0))
    w = seasonal * weekday
    w = w / w.sum()

    # ---- invoices first, then lines within them -------------------------
    n_invoices = max(1, n_rows // 19)
    inv_dates = pd.to_datetime(rng.choice(dates.to_numpy(), size=n_invoices, p=w))
    # Trading hours, concentrated around midday.
    minutes = np.clip(rng.normal(12 * 60, 2.2 * 60, n_invoices), 7 * 60, 20 * 60)
    inv_dates = inv_dates + pd.to_timedelta(minutes.astype(int), unit="m")

    cust_idx = rng.choice(
        len(customers), size=n_invoices,
        p=customers["weight"] / customers["weight"].sum(),
    )
    # Basket size: mostly small, occasional big wholesale order.
    lines = np.clip(rng.negative_binomial(2.2, 0.11, n_invoices), 1, 380)
    scale = n_rows / max(lines.sum(), 1)
    lines = np.maximum(1, (lines * scale).astype(int))

    invoice_no = np.repeat(np.arange(489434, 489434 + n_invoices).astype(str), lines)
    invoice_date = np.repeat(inv_dates.to_numpy(), lines)
    cust_rep = np.repeat(customers["customer_id"].to_numpy()[cust_idx], lines)
    country_rep = np.repeat(customers["country"].to_numpy()[cust_idx], lines)
    total = len(invoice_no)

    prod_idx = rng.choice(len(catalogue), size=total, p=catalogue["popularity"].to_numpy())
    chosen = catalogue.iloc[prod_idx]

    # Wholesale buying: quantities cluster on 1, 2, 6, 12, 24, 48.
    qty_values = np.array([1, 2, 3, 4, 6, 8, 10, 12, 24, 36, 48, 72, 96, 120])
    qty_p = np.array([.20, .13, .07, .06, .13, .04, .06, .18, .06, .02,
                      .025, .01, .005, .005])
    qty = rng.choice(qty_values, size=total, p=qty_p / qty_p.sum())
    # Price varies slightly by order, as it does in the real file.
    price = np.round(chosen["base_price"].to_numpy() * rng.normal(1.0, 0.06, total), 2)
    price = np.clip(price, 0.04, None)

    df = pd.DataFrame({
        "Invoice": invoice_no,
        "StockCode": chosen["stock_code"].to_numpy(),
        "Description": chosen["description"].to_numpy(),
        "Quantity": qty,
        "InvoiceDate": invoice_date,
        "Price": price,
        "Customer ID": cust_rep,
        "Country": country_rep,
    })
    return df


def inject_real_world_mess(df: pd.DataFrame, rng) -> pd.DataFrame:
    """Reproduce the specific defects this dataset is known for."""
    n = len(df)
    extra = []

    # 1. Guest checkouts - roughly a fifth of rows have no Customer ID.
    guest = rng.random(n) < 0.216
    df.loc[guest, "Customer ID"] = np.nan

    # 2. Cancellations: invoice prefixed 'C', negative quantity, later date.
    cancels = df.sample(max(5, int(n * 0.021)), random_state=1).copy()
    cancels["Invoice"] = "C" + cancels["Invoice"].astype(str)
    cancels["Quantity"] = -cancels["Quantity"].abs()
    cancels["InvoiceDate"] = cancels["InvoiceDate"] + pd.to_timedelta(
        rng.integers(1, 45, len(cancels)), unit="D"
    )
    # A credit cannot be raised after the extract was taken.
    cancels["InvoiceDate"] = cancels["InvoiceDate"].clip(upper=df["InvoiceDate"].max())
    extra.append(cancels)

    # 3. Service lines - postage, manual adjustments, fees.
    n_service = max(5, int(n * 0.017))
    base = df.sample(n_service, random_state=2).copy()
    picks = rng.integers(0, len(SERVICE), n_service)
    base["StockCode"] = [SERVICE[i][0] for i in picks]
    base["Description"] = [SERVICE[i][1] for i in picks]
    base["Price"] = [round(SERVICE[i][2] * float(rng.normal(1, .08)), 2) for i in picks]
    base["Quantity"] = 1
    extra.append(base)

    # 4. Gift vouchers, which carry their own code family.
    vouchers = df.sample(max(3, int(n * 0.0012)), random_state=3).copy()
    vouchers["StockCode"] = [f"gift_0001_{i:02d}" for i in
                             rng.integers(10, 90, len(vouchers))]
    vouchers["Description"] = "Dotcomgiftshop Gift Voucher"
    vouchers["Quantity"] = 1
    extra.append(vouchers)

    # 5. Warehouse annotations where a product name should be.
    junk = df.sample(max(4, int(n * 0.0035)), random_state=4).copy()
    junk["Description"] = rng.choice(JUNK_DESCRIPTIONS, len(junk))
    junk["Price"] = 0.0
    junk["Customer ID"] = np.nan
    junk["Quantity"] = rng.integers(-600, 600, len(junk))
    extra.append(junk)

    # 6. Exact duplicate rows from a replayed load.
    extra.append(df.sample(max(4, int(n * 0.0045)), random_state=5))

    # 7. Zero-price giveaways and samples.
    free = df.sample(max(3, int(n * 0.0018)), random_state=6).copy()
    free["Price"] = 0.0
    extra.append(free)

    # 8. Missing descriptions.
    nodesc = df.sample(max(3, int(n * 0.0025)), random_state=7).copy()
    nodesc["Description"] = np.nan
    extra.append(nodesc)

    # 9. Untrimmed and inconsistently cased text from the source system.
    messy = df.sample(max(3, int(n * 0.004)), random_state=8).copy()
    messy["Description"] = "  " + messy["Description"].astype(str).str.lower() + " "
    messy["StockCode"] = messy["StockCode"].astype(str) + " "
    extra.append(messy)

    # 10. A decimal-point slip.
    slip = df.sample(max(2, int(n * 0.0004)), random_state=9).copy()
    slip["Price"] = slip["Price"] * 10000
    extra.append(slip)

    out = pd.concat([df] + extra, ignore_index=True)
    return out.sort_values("InvoiceDate").reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate an Online Retail II-shaped sample extract")
    ap.add_argument("--rows", type=int, default=250_000,
                    help="approximate clean rows before defects are injected")
    ap.add_argument("--products", type=int, default=3800)
    ap.add_argument("--customers", type=int, default=5400)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--start", default="2009-12-01")
    ap.add_argument("--end", default="2011-12-09")   # the real file stops here
    ap.add_argument("--outfile", default="online_retail_II_sample.csv")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    OUT.mkdir(parents=True, exist_ok=True)

    catalogue = build_catalogue(rng, args.products)
    customers = build_customers(rng, args.customers)
    df = build_transactions(rng, args.rows, catalogue, customers, args.start, args.end)
    df = inject_real_world_mess(df, rng)

    # Match the source file's own formatting quirks.
    df["Customer ID"] = df["Customer ID"].astype("Int64")
    path = OUT / args.outfile
    df.to_csv(path, index=False)

    cancelled = df["Invoice"].astype(str).str.upper().str.startswith("C").sum()
    print(f"rows              {len(df):>10,}")
    print(f"invoices          {df['Invoice'].nunique():>10,}")
    print(f"stock codes       {df['StockCode'].nunique():>10,}")
    print(f"customers         {df['Customer ID'].nunique():>10,}")
    print(f"missing customer  {df['Customer ID'].isna().sum():>10,} "
          f"({df['Customer ID'].isna().mean():.1%})")
    print(f"cancellation rows {cancelled:>10,} ({cancelled / len(df):.1%})")
    print(f"date range        {df['InvoiceDate'].min():%Y-%m-%d} to "
          f"{df['InvoiceDate'].max():%Y-%m-%d}")
    print(f"\nwritten to {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
