from __future__ import annotations
import argparse
from datetime import datetime, timezone

from quant_platform.storage import open_store
from quant_platform.ingest.binance_data_vision import (
    monthly_kline,fetch_archive,parse_usdm_kline_zip,normalize_5m,
)
from quant_platform.ingest.plan import discovery_plan
from quant_platform.ingest.remote_publish import ingest_month_to_store


def run_id(year, month):
    # Deterministic ID makes accidental repeated ingestion obvious.
    return f"INGEST-BINANCE-BTCUSDT-5M-{year:04d}-{month:02d}-v1"


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--store-uri",required=True)
    p.add_argument("--start-index",type=int,default=0)
    p.add_argument("--end-index",type=int,default=None)
    a=p.parse_args()

    store=open_store(a.store_uri)
    plan=discovery_plan()[a.start_index:a.end_index]

    for task in plan:
        archive=monthly_kline("BTCUSDT","5m",task.year,task.month)
        print("FETCH",archive.filename,flush=True)
        fetched=fetch_archive(archive)
        frame=normalize_5m(parse_usdm_kline_zip(fetched.payload))
        manifest=ingest_month_to_store(
            fetched=fetched,
            frame=frame,
            store=store,
            instrument_id="BTCUSDT-PERP.BINANCE",
            run_id=run_id(task.year,task.month),
        )
        print(
            "PUBLISHED",archive.filename,
            "rows=",manifest["rows"],
            "sha=",manifest["actual_sha256"],
            flush=True,
        )

if __name__=="__main__":
    main()
