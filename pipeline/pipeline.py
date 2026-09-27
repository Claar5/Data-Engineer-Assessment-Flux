"""Orchestrator + CLI: ingest -> transform -> load, for properties, FX rates,
then each bookings batch in filename order.

    python -m pipeline.pipeline                        # process everything found
    python -m pipeline.pipeline --batch path/to.csv     # (re)process one batch
"""
import argparse
import logging
import uuid
from pathlib import Path

import pandas as pd

from . import config, ingest, load, transform

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("flux_pipeline")


def run(batch_paths=None):
    run_id = str(uuid.uuid4())
    run_ts = pd.Timestamp.now()
    conn = load.connect(config.WAREHOUSE_DB)

    staged = ingest.ingest_properties(config.RAW_DIR / "properties.csv")
    prepped = transform.diff_properties(staged, load.read_dim_property_current(conn))
    counts = load.load_properties(conn, prepped, run_ts)
    load.write_merge_audit(conn, run_id, "dim_property", counts, run_ts)
    logger.info("dim_property: %s", counts)

    staged = ingest.ingest_fx_rates(config.RAW_DIR / "fx_rates.csv")
    prepped = transform.diff_fx_rates(staged, load.read_dim_fx_all(conn))
    counts = load.load_fx_rates(conn, prepped, run_ts)
    load.write_merge_audit(conn, run_id, "dim_fx_rate", counts, run_ts)
    logger.info("dim_fx_rate: %s", counts)

    dim_property_current = load.read_dim_property_current(conn)
    dim_fx_all = load.read_dim_fx_all(conn)
    valid_property_ids = set(dim_property_current["property_id"])

    batches = batch_paths or sorted(config.RAW_DIR.glob("bookings_batch*.csv"))
    for batch_path in batches:
        batch_path = Path(batch_path)
        logger.info("Processing %s", batch_path.name)

        staged = ingest.ingest_bookings(batch_path)
        good, rejects = transform.split_valid_bookings(staged, valid_property_ids, dim_fx_all)
        load.write_rejects(conn, rejects, run_id, run_ts)

        deduped = transform.dedupe_bookings(good)
        current_fact = load.read_fact_latest(conn)
        classified = transform.classify_bookings(deduped, current_fact)
        keyed = transform.resolve_property_key(classified, dim_property_current)
        keyed = transform.resolve_fx_key(keyed, dim_fx_all)

        counts = load.load_bookings(conn, keyed, run_ts)
        load.write_merge_audit(conn, run_id, "fact_bookings", counts, run_ts)
        logger.info("fact_bookings %s: %s (%d rejected)", batch_path.name, counts, len(rejects))

    conn.close()
    logger.info("Run %s complete", run_id)
    return run_id


def main():
    parser = argparse.ArgumentParser(description="Flux bookings pipeline")
    parser.add_argument("--batch", action="append", help="Bookings batch CSV to (re)process; repeatable")
    args = parser.parse_args()
    run(args.batch)


if __name__ == "__main__":
    main()
