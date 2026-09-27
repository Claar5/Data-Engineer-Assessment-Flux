"""Ingest: read the raw CSVs, clean values, rename columns to stable names.

Everything here is a pandas Series operation (vectorised), not a per-row
loop. No business decisions (valid/invalid, new/changed) are made here --
that's transform.py's job.
"""
import pandas as pd

from . import config

DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d %b %Y"]  # formats seen in the source extracts


def _clean_text(series):
    return series.astype("string").str.strip().str.replace(r"\s+", " ", regex=True)


def _map_strict(series, aliases):
    """Map free text to a canonical value via a lower-cased alias lookup.
    No match -> NaN (so validation can reject it)."""
    return _clean_text(series).str.lower().map(aliases)


def _map_or_keep(series, aliases):
    """Same, but no match keeps the original text instead of becoming NaN."""
    cleaned = _clean_text(series)
    return cleaned.str.lower().map(aliases).fillna(cleaned)


def _parse_money(series):
    s = series.astype("string").str.strip().str.replace(",", "", regex=False).replace("", pd.NA)
    return pd.to_numeric(s, errors="coerce")


def _parse_dates(series):
    """A column mixing several date formats -> one datetime64 column.
    Each known format fills in whatever the earlier ones couldn't parse."""
    s = series.astype("string").str.strip()
    result = pd.to_datetime(s, format=DATE_FORMATS[0], errors="coerce")
    for fmt in DATE_FORMATS[1:]:
        result = result.fillna(pd.to_datetime(s, format=fmt, errors="coerce"))
    return result


def read_csv(path):
    return pd.read_csv(path, dtype="string", na_values=["", " "])


def ingest_bookings(path):
    raw = read_csv(path)
    df = pd.DataFrame({
        "booking_id": _clean_text(raw["booking_id"]).str.upper(),
        "property_id": _clean_text(raw["property_id"]).str.upper(),
        "check_in_date": _parse_dates(raw["check_in"]),
        "check_out_date": _parse_dates(raw["check_out"]),
        "num_guests": pd.to_numeric(raw["num_guests"], errors="coerce").astype("Int64"),
        "room_rate": _parse_money(raw["room_rate"]),
        "currency_code": _map_strict(raw["currency"], config.CURRENCY_ALIASES),
        "revenue_amount": _parse_money(raw["revenue"]),
        "booking_channel": _map_strict(raw["booking_channel"], config.CHANNEL_ALIASES),
        "booking_status": _clean_text(raw["booking_status"]).str.lower(),
        "created_at": pd.to_datetime(raw["created_at"].str.strip(), errors="coerce"),
        "updated_at": pd.to_datetime(raw["updated_at"].str.strip(), errors="coerce"),
    })
    df["source_batch"] = path.name
    df["source_row"] = df.index.astype("int64")  # original file order, used as a tiebreak later
    return df


def ingest_properties(path):
    raw = read_csv(path)
    return pd.DataFrame({
        "property_id": _clean_text(raw["property_id"]).str.upper(),
        "property_name": _clean_text(raw["property_name"]),
        "country": _map_or_keep(raw["country"], config.COUNTRY_ALIASES),
        "region": _clean_text(raw["region"]),
        "room_count": pd.to_numeric(raw["room_count"], errors="coerce").astype("Int64"),
        "property_status": _clean_text(raw["property_status"]).str.lower(),
    })


def ingest_fx_rates(path):
    raw = read_csv(path)
    return pd.DataFrame({
        "currency_code": _map_strict(raw["currency"], config.CURRENCY_ALIASES),
        "rate_to_zar": _parse_money(raw["rate_to_zar"]),
        "effective_from": _parse_dates(raw["effective_from"]),
    })
