"""Plain settings -- no framework, just the values the pipeline needs."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
WAREHOUSE_DB = PROJECT_ROOT / "data" / "warehouse" / "flux.db"
# lower-cased free-text -> canonical value
CURRENCY_ALIASES = {
    "usd": "USD", "us$": "USD", "$": "USD",
    "eur": "EUR", "€": "EUR",
    "gbp": "GBP", "£": "GBP",
    "zar": "ZAR", "r": "ZAR",  # assumption: "R" means Rand, not another currency
}
CHANNEL_ALIASES = {
    "direct": "Direct",
    "ota": "OTA", "o.t.a": "OTA",
    "website": "Website",
    "travel agent": "Travel Agent",
}
COUNTRY_ALIASES = {
    "s. africa": "South Africa",  # properties.csv has two spellings for the same country
}
VALID_STATUSES = {"confirmed", "pending", "cancelled"}

MIN_NIGHTS = 1
MIN_GUESTS = 1
