# Interactive Data Visualization Library for Business Intelligence
## Engineering notes

**One line:** A production-shaped BI pipeline over 1.07M real retail transactions —
Python/pandas ingestion and cleaning, a validation gate that can abort the load,
a PostgreSQL snowflake schema with row-level security, SQL analytics views, and
Power BI dashboards fed from versioned CSV extracts.

---

## 1. Dataset

UCI **Online Retail II** — a UK-based online gift wholesaler, Dec 2009 to Dec 2011.

| Property | Value |
|---|---|
| Source rows | 1,067,371 across two worksheets |
| Retained | 1,026,355 (96.2%) |
| Quarantined | 41,016 |
| Date span | 2009-12-01 to 2011-12-09 |
| Countries | 43 |
| Identified customers | 5,941 |

Chosen because it is genuinely dirty: cancellations, service codes, guest checkouts,
warehouse annotations and duplicate rows all appear naturally. Nothing was synthesised.

---

## 2. Architecture

Dataset -> Python/Pandas -> Cleaning & Validation -> PostgreSQL -> SQL Analytics
-> Visualization Library -> Power BI -> Monitoring

The validation gate sits **before** the load, not after. ERROR-severity failures
abort the run; WARN failures are recorded and the load continues. The point is that
bad data never reaches the warehouse, so downstream consumers never see a
half-correct table they have to reason about.

The database then re-verifies independently what Python asserted, via primary keys,
foreign keys and CHECK constraints. Two independent statements of the same rule is
deliberate: a bug in the pandas logic does not silently become a bug in the warehouse.

---

## 3. Verified figures

These are reproducible on every run and are the identity used to prove correctness:

| Metric | Value |
|---|---|
| Gross revenue | £19,464,639.76 |
| Net revenue | £18.2M |
| Returns value | £1.3M |
| Return rate | 6.7% |
| Orders | 40,122 |
| Average order value | £485 |
| Units sold | 11,104,561 |
| Distinct products sold | 4,726 |
| Guest checkout share | 22.3% |

---

## 4. Cleaning decisions and why

Every defect got a **decision**, not a deletion. The distinction matters because
deleting rows silently changes revenue.

| Defect | Rows | Decision | Reasoning |
|---|---|---|---|
| Exact duplicate rows | 34,337 | quarantined | a replayed load double-counts revenue |
| Cancellations (`C` prefix) | 19,104 | **kept, netted off** | returns are real business events; deleting them overstates revenue by £1.3M |
| Service lines (POST, DOT, M, fees, vouchers) | 5,869 | flagged, kept | real money, but not products — must not enter product rankings |
| Warehouse annotations ("damaged", "check", "?") | 1,491 | quarantined | stock adjustments, not sales |
| Non-positive prices | 5,181 | quarantined | giveaways carry no revenue signal |
| Negative qty outside a credit note | 5 | quarantined | unexplained; too few to model |
| Extreme quantities (>80,000) | 2 | quarantined | bulk corrections |
| Guest checkouts (no customer ID) | 229,018 | **kept for revenue, excluded from RFM** | 22.3% of rows; dropping them loses real sales, keeping them corrupts cohort analysis |

The guest-checkout split is the decision worth defending: the same rows are
**in scope for revenue and out of scope for customer analytics**, because a
null customer ID cannot be tracked across time but its money still arrived.

---

## 5. Validation

25 rules across five categories: Completeness, Consistency, Validity,
Referential, Uniqueness. Current state — **21/25 pass**, four WARN, zero ERROR.

The four warnings are all expected properties of the source data, not defects:

- *Product has a description* — 4,382 nulls (0.41%) in the source
- *Customer is identified* — the 22.3% guest checkouts, by design
- *Credit notes contain no positive product value* — one mixed invoice
- *Line revenue has no extreme outliers* — genuine bulk orders

A check that warns forever is only useful if you know why. These are documented
rather than suppressed, so a *new* warning is a real signal.

---

## 6. Snowflake schema

The star schema was normalised into a snowflake: nine dimension tables replacing
four, with `dim_region -> dim_country_snow`, `dim_cohort -> dim_customer_snow`,
`dim_year -> dim_month -> dim_date_snow`, `dim_product_type -> dim_product_snow`.

| Table | Rows |
|---|---|
| dim_region | 3 |
| dim_country_snow | 43 |
| dim_cohort | 25 |
| dim_customer_snow | 5,941 |
| dim_year | 3 |
| dim_month | 25 |
| dim_date_snow | 739 |
| dim_product_type | 3 |
| dim_product_snow | 4,752 |

**The trade-off (Kimball):** snowflaking removes redundancy but costs joins and
confuses BI tools. Resolved by keeping the normalised tables as the source of
truth and exposing `core.vw_dim_*_flat` star-shaped views on top. Analytics and
Power BI read the flat views; the snowflake holds the constraints.

**Proof it is lossless:** after re-pointing all analytics views at the flattening
views, gross revenue stayed at **£19,464,639.76** and orders at **40,122**.
Identical totals through a completely different join path is the evidence — a
dropped or duplicated row anywhere in the decomposition would move the number.

---

## 7. Row-level security

Six roles, policies on `core.fact_sales`, driven by a `security.user_access` table.

| Role | Rows visible | Scope |
|---|---|---|
| superuser | 1,026,355 | bypasses RLS |
| bi_cfo | 1,026,355 | GLOBAL |
| bi_etl | 1,026,355 | load path |
| bi_uk | 941,694 | COUNTRY = United Kingdom |
| bi_eu | 79,157 | REGION = Europe |
| bi_reader | **0** | unmapped |

941,694 + 79,157 = 1,020,851, leaving 5,504 rest-of-world rows visible only to
GLOBAL roles. The partition is coherent, not overlapping.

**Three non-obvious things this implementation gets right:**

1. **Fail-closed.** `bi_reader` has no row in `user_access`, so it sees **zero**
   rows, not all rows. Unmapped users getting everything is the classic RLS leak.

2. **Set-returning policy function.** The first version called a per-row function
   and never returned on 1M rows. Replaced with `visible_country_keys()` used as
   `country_key IN (SELECT ...)`, which PostgreSQL evaluates once per statement
   as an InitPlan. **Never -> 250 ms.**

3. **`REVOKE EXECUTE ... FROM PUBLIC`.** PostgreSQL grants EXECUTE on new
   functions to PUBLIC by default, so any user could call `set_current_user()`
   and widen their own scope. Verified: `bi_eu` attempting to become
   `export.analyst@retailco.com` is denied and stays at 79,157 rows.

Also: `SECURITY DEFINER` initially resolved every caller to `postgres`. Fixed by
passing the caller role as an argument evaluated inside the policy expression.
And nine analytics views needed `security_invoker = true` — a view otherwise runs
as its owner and bypasses every policy beneath it.

---

## 8. Problems solved

| Problem | Fix |
|---|---|
| `ROUND(double precision, int)` does not exist in PostgreSQL | pinned float columns to `NUMERIC(18,6)` |
| FK type mismatch (double vs bigint) | forced `*_key` columns to `Int64` / `BigInteger` |
| Multi-row INSERT hit the 65,535 parameter ceiling | bulk load via `COPY` |
| `Timedelta.__format__` error in RFM | cast `MAX(full_date)::date` so subtraction yields integer days |
| Cluster corruption (`0xC000013A`) after Ctrl+C during writes | kill processes, delete `.pgdata`, rebuild — pipeline is fully idempotent |

That last one is worth stating plainly: the entire warehouse was destroyed and
rebuilt from source in 138 seconds, reproducing every figure exactly. That is
what makes the pipeline trustworthy rather than merely working.

---

## 9. Limitations (stated honestly)

- **No cost data.** Online Retail II has no cost of goods, so there is no margin
  or profit analysis. None was fabricated. A dashboard showing "Profit % 100.00%"
  is a broken card, not an insight.
- **Two years, one retailer.** Seasonality is real but not generalisable.
- **`pgserver` is embedded PostgreSQL** — correct for a portable project, not a
  substitute for a managed cluster.
- **RLS under Power BI import mode** is enforced by the report, not the database;
  the database policies bind under DirectQuery. Both layers read the same
  `user_access` table so the rules cannot drift apart.

---

## 10. Questions to expect

**Why keep cancellations instead of deleting them?**
Deleting them overstates revenue by £1.3M and makes the 6.7% return rate
unmeasurable. They are netted off.

**Why validate before loading rather than after?**
So bad data never reaches consumers. Post-load validation means someone has
already queried the wrong number.

**Why snowflake if star is the BI standard?**
The snowflake is where the constraints live; the flattening views give BI tools
the star they want. Both, not either.

**How do you know the normalisation did not lose data?**
Gross revenue and order count are unchanged through an entirely different set
of joins.

**What breaks first at 10x the data?**
The Excel read and the pandas cleaning, both single-machine and in-memory. The
SQL layer and RLS are already set-based and would hold. The fix is chunked or
columnar ingestion, not a schema change.
