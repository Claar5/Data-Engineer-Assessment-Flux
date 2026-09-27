**AI was used to generate this file**

# Flux Data Engineer Assessment

A small batch pipeline that loads messy booking extracts into a SQLite warehouse,
plus the SQL used for the reporting questions.

## Layout

## Running it

```bash
python -m pipeline.pipeline # process every batch found
python -m pipeline.pipeline --batch data/raw/bookings_batch2_2025-09-02.csv
```

The run is idempotent: re-running a batch writes nothing new.
The data is in a sqllite database and SQL queries are compatible with that only.

## How it works

**Ingest** reads the CSVs as strings and normalises them: three date formats,
currency symbols and aliases (`$`, `US$`, `R`, `€`) to ISO codes, channel spellings
to canonical values, thousands separators stripped from money.

**Transform** rejects rows that can't be trusted (bad dates, non-positive revenue,
unknown currency, orphan property, invalid status), keeps one row per `booking_id`
(latest `updated_at`), then tags every row insert / update / unchanged against what
is already stored. FX conversion is point-in-time: each booking uses the rate in
effect on its `created_at`.

**Load** writes to SQLite. `dim_property` and `dim_fx_rate` are SCD2, so history is
expired rather than overwritten. `fact_bookings` is append-only, one row per booking
version. Rejected rows go to `rejects` with a reason, and every run records row
counts per action in `merge_audit`.

## Analysis

`analysis/bookings_sqlite.sql` builds three views (current booking version,
point-in-time ZAR revenue, and a reporting view joined to property attributes) and
answers the month-on-month growth and top-properties-per-country questions.
Revenue is recognised on confirmed bookings only; pending and cancelled amounts are
kept in separate columns.
Unfortunately BigQuery compatible code could not be written at this time

## Assumptions

- `R` means South African Rand.
- `S. Africa` and `South Africa` are the same country.
- A booking whose `created_at` predates the earliest FX rate for its currency is rejected
  rather than converted at a guessed rate.
- FX rates arrive per currency in non-decreasing `effective_from` order.