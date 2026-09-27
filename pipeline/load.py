"""Load: SQLite warehouse -- SCD2 dimension writers, fact append, rejects and
merge audit. Nothing overwrites history: dims expire + insert, and the fact gets
a new row per booking version (only for rows classified insert/update)."""
import sqlite3

import pandas as pd

from .transform import PROPERTY_COLS

SCHEMA = """
CREATE TABLE IF NOT EXISTS dim_property (
    property_sk INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id TEXT, property_name TEXT, country TEXT, region TEXT,
    room_count INTEGER, property_status TEXT,
    valid_from TEXT, valid_to TEXT, is_current INTEGER, loaded_at TEXT
);
CREATE TABLE IF NOT EXISTS dim_fx_rate (
    fx_sk INTEGER PRIMARY KEY AUTOINCREMENT,
    currency_code TEXT, rate_to_zar REAL,
    valid_from TEXT, valid_to TEXT, is_current INTEGER, loaded_at TEXT
);
CREATE TABLE IF NOT EXISTS fact_bookings (
    booking_sk INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id TEXT, property_sk INTEGER, fx_sk INTEGER,
    check_in_date TEXT, check_out_date TEXT, num_guests INTEGER, room_rate REAL,
    currency_code TEXT, revenue_amount REAL, revenue_zar REAL,
    booking_channel TEXT, booking_status TEXT, is_cancelled INTEGER,
    created_at TEXT, updated_at TEXT, source_batch TEXT, loaded_at TEXT,
    UNIQUE (booking_id, updated_at)
);
CREATE TABLE IF NOT EXISTS rejects (
    reject_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, booking_id TEXT,
    reject_reason TEXT, source_batch TEXT, rejected_at TEXT
);
CREATE TABLE IF NOT EXISTS merge_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, table_name TEXT,
    action TEXT, row_count INTEGER, run_at TEXT
);
"""

FACT_COLS = [
    "booking_id", "property_sk", "fx_sk", "check_in_date", "check_out_date", "num_guests",
    "room_rate", "currency_code", "revenue_amount", "revenue_zar", "booking_channel",
    "booking_status", "is_cancelled", "created_at", "updated_at", "source_batch", "loaded_at",
]


def connect(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def _iso(ts):
    return None if pd.isna(ts) else pd.Timestamp(ts).isoformat(sep=" ")


def _append(conn, df, table):
    """to_sql turns NaN/NA into NULL and numpy types into plain Python ones."""
    df.to_sql(table, conn, if_exists="append", index=False)
    conn.commit()


def read_dim_property_current(conn):
    return pd.read_sql_query(
        "SELECT * FROM dim_property WHERE is_current = 1", conn, parse_dates=["valid_from", "valid_to"]
    )


def read_dim_fx_all(conn):
    return pd.read_sql_query("SELECT * FROM dim_fx_rate", conn, parse_dates=["valid_from", "valid_to"])


def read_fact_latest(conn):
    """Latest stored updated_at per booking_id."""
    return pd.read_sql_query(
        "SELECT booking_id, MAX(updated_at) AS updated_at FROM fact_bookings GROUP BY booking_id",
        conn, parse_dates=["updated_at"],
    )


# ---------------------------------------------------------------------------
# Dimensions (SCD2)
# ---------------------------------------------------------------------------

def load_properties(conn, prepped, run_ts):
    run_ts_iso = _iso(run_ts)
    conn.executemany(
        "UPDATE dim_property SET valid_to = ?, is_current = 0 WHERE property_id = ? AND is_current = 1",
        [(run_ts_iso, pid) for pid in prepped.loc[prepped["action"] == "update", "property_id"]],
    )
    new = prepped.loc[prepped["action"].isin(["insert", "update"]), ["property_id"] + PROPERTY_COLS]
    _append(conn, new.assign(valid_from=run_ts_iso, is_current=1, loaded_at=run_ts_iso), "dim_property")
    return prepped["action"].value_counts().to_dict()


def load_fx_rates(conn, prepped, run_ts):
    run_ts_iso = _iso(run_ts)
    cur = conn.cursor()
    inserts = prepped.loc[prepped["action"] == "insert"].sort_values(["currency_code", "effective_from"])

    for r in inserts.itertuples():
        effective_from = _iso(r.effective_from)
        current = cur.execute(
            "SELECT fx_sk FROM dim_fx_rate WHERE currency_code = ? AND is_current = 1", (r.currency_code,)
        ).fetchone()
        if current:
            cur.execute("UPDATE dim_fx_rate SET valid_to = ?, is_current = 0 WHERE fx_sk = ?", (effective_from, current[0]))
        cur.execute(
            "INSERT INTO dim_fx_rate (currency_code, rate_to_zar, valid_from, valid_to, is_current, loaded_at) "
            "VALUES (?, ?, ?, NULL, 1, ?)",
            (r.currency_code, float(r.rate_to_zar), effective_from, run_ts_iso),
        )
    conn.commit()
    return prepped["action"].value_counts().to_dict()


# ---------------------------------------------------------------------------
# Fact (append-only: each update is a new row)
# ---------------------------------------------------------------------------

def load_bookings(conn, df, run_ts):
    writable = df.loc[df["action"].isin(["insert", "update"])].assign(
        is_cancelled=lambda d: (d["booking_status"] == "cancelled").astype(int),
        loaded_at=_iso(run_ts),
    )
    _append(conn, writable[FACT_COLS], "fact_bookings")
    return df["action"].value_counts().to_dict()


# ---------------------------------------------------------------------------
# Rejects / merge audit
# ---------------------------------------------------------------------------

def write_rejects(conn, rejects, run_id, ts):
    rows = rejects[["booking_id", "reject_reason", "source_batch"]].assign(run_id=run_id, rejected_at=_iso(ts))
    _append(conn, rows, "rejects")


def write_merge_audit(conn, run_id, table_name, counts, ts):
    rows = pd.DataFrame({"action": list(counts), "row_count": list(counts.values())})
    _append(conn, rows.assign(run_id=run_id, table_name=table_name, run_at=_iso(ts)), "merge_audit")
