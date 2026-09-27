"""Transform: reject bad rows, dedupe, classify against current state, and
resolve dimension keys.

One shared vocabulary throughout: every row -- fact or dimension -- ends up
tagged insert / update / unchanged. load.py only ever writes insert/update,
which is what makes re-running the pipeline (or replaying an old batch)
safe: an unchanged row, or a stale update whose updated_at is not newer than
what's already stored, is never sent to the database.
"""
import numpy as np
import pandas as pd

from . import config

PROPERTY_COLS = ["property_name", "country", "region", "room_count", "property_status"]


# ---------------------------------------------------------------------------
# Bookings (fact)
# ---------------------------------------------------------------------------

def split_valid_bookings(df, valid_property_ids, dim_fx):
    """Reject rows too broken to trust; return (good, rejects_with_reason).
    A row failing more than one rule appears once per reason in `rejects`,
    but only ever once (or not at all) in `good`."""
    nights = (df["check_out_date"] - df["check_in_date"]).dt.days
    earliest_rate = dim_fx.groupby("currency_code")["valid_from"].min()
    has_fx_rate = pd.to_datetime(df["currency_code"].map(earliest_rate)) <= df["created_at"]

    checks = [
        (df["created_at"].isna() | df["updated_at"].isna(), "invalid_timestamp"),
        (df["updated_at"] < df["created_at"], "updated_before_created"),
        (df["check_in_date"].isna() | df["check_out_date"].isna() | (nights < config.MIN_NIGHTS), "invalid_date_range"),
        (df["num_guests"].isna() | (df["num_guests"] < config.MIN_GUESTS), "invalid_guests"),
        (df["revenue_amount"].isna(), "missing_revenue"),
        (df["revenue_amount"] <= 0, "non_positive_revenue"),
        (df["currency_code"].isna(), "unknown_currency"),
        (df["currency_code"].notna() & ~has_fx_rate, "no_applicable_fx_rate"),
        (~df["property_id"].isin(valid_property_ids), "orphan_property"),
        (~df["booking_status"].isin(config.VALID_STATUSES), "invalid_status"),
    ]
    checks = [(mask.fillna(False).astype(bool), reason) for mask, reason in checks]

    reject_mask = pd.concat([mask for mask, _ in checks], axis=1).any(axis=1)
    rejects = pd.concat([df.loc[mask].assign(reject_reason=reason) for mask, reason in checks], ignore_index=True)
    good = df.loc[~reject_mask].copy()
    good["booking_channel"] = good["booking_channel"].fillna("Unknown")
    return good.reset_index(drop=True), rejects


def dedupe_bookings(df):
    """One row per booking_id: keep the latest by updated_at. Ties (same
    updated_at, e.g. resent/conflicting rows) are broken by source_row so
    the result is the same no matter how many times the file is reprocessed."""
    ordered = df.sort_values(["booking_id", "updated_at", "source_row"])
    return ordered.drop_duplicates("booking_id", keep="last").reset_index(drop=True)


def classify_bookings(df, current_fact):
    """insert / update / unchanged, based on updated_at vs. the latest version
    stored for that booking_id. Both insert and update become a new fact row.
    A stale or re-sent row (updated_at <= latest stored) classifies unchanged
    and is never written."""
    stored = pd.to_datetime(df["booking_id"].map(current_fact.set_index("booking_id")["updated_at"]))
    action = np.select([stored.isna(), df["updated_at"] > stored], ["insert", "update"], default="unchanged")
    return df.assign(action=action)


def resolve_property_key(df, dim_property_current):
    """Attach property_sk from the current dimension row."""
    return df.merge(dim_property_current[["property_sk", "property_id"]], on="property_id", how="left")


def resolve_fx_key(df, dim_fx):
    """Point-in-time FX join: attach the fx_sk/rate_to_zar version effective
    on each booking's created_at, and compute revenue_zar."""
    fx = dim_fx[["currency_code", "fx_sk", "rate_to_zar", "valid_from"]]
    # merge_asof needs identical key dtypes on both sides
    out = pd.merge_asof(
        df.astype({"currency_code": str}).sort_values("created_at"),
        fx.astype({"currency_code": str}).sort_values("valid_from"),
        left_on="created_at", right_on="valid_from", by="currency_code", direction="backward",
    ).drop(columns="valid_from")
    out["revenue_zar"] = (out["revenue_amount"] * out["rate_to_zar"]).round(2)
    return out


# ---------------------------------------------------------------------------
# Dimensions (SCD2 change detection)
# ---------------------------------------------------------------------------

def diff_properties(staged, dim_property_current):
    """insert / update / unchanged vs. the current dim_property row."""
    staged = staged.drop_duplicates("property_id", keep="last")
    # SQLite hands room_count back as float when any value is NULL; match ingest's Int64
    current = dim_property_current[["property_id"] + PROPERTY_COLS].astype({"room_count": "Int64"})
    merged = staged.merge(current, on="property_id", how="left", suffixes=("", "_now"), indicator=True)

    is_new = merged["_merge"] == "left_only"
    changed = pd.Series(False, index=merged.index)
    for col in PROPERTY_COLS:
        # NULL on both sides counts as equal
        changed |= merged[col].astype("string").fillna("") != merged[f"{col}_now"].astype("string").fillna("")

    merged["action"] = np.select([is_new, changed.astype(bool)],["insert", "update"], default="unchanged")
    return merged[["property_id"] + PROPERTY_COLS + ["action"]]


def diff_fx_rates(staged, dim_fx_all):
    """New (currency, effective_from) versions vs. what's already loaded.
    Assumes rates arrive in non-decreasing effective_from order per
    currency, which holds for this feed."""
    staged = staged.dropna(subset=["currency_code", "effective_from"])
    staged = staged.drop_duplicates(subset=["currency_code", "effective_from"]).copy()

    existing = set(zip(dim_fx_all["currency_code"], dim_fx_all["valid_from"]))
    staged["action"] = [
        "unchanged" if key in existing else "insert"
        for key in zip(staged["currency_code"], staged["effective_from"])
    ]
    return staged
