from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone

from quant_platform.storage import (
    exists,
    open_store,
    resolve_store_uri,
    write_bytes,
)
from quant_platform.ingest.binance_data_vision import (
    fetch_archive,
    monthly_kline,
    normalize_5m,
    parse_usdm_kline_zip,
)
from quant_platform.ingest.plan import discovery_plan
from quant_platform.ingest.remote_publish import ingest_month_to_store
from quant_platform.ingest.remote_snapshot import freeze_discovery_snapshot
from quant_platform.research.exp001_remote import main as exp001_main


def run_id(year: int, month: int) -> str:
    return f"INGEST-BINANCE-BTCUSDT-5M-{year:04d}-{month:02d}-v1"


def environment_fingerprint() -> dict:
    try:
        freeze = subprocess.check_output(
            [sys.executable, "-m", "pip", "freeze"],
            text=True,
            timeout=60,
        )
    except Exception as exc:
        freeze = f"pip-freeze-unavailable: {exc}"

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "pip_freeze": freeze.splitlines(),
        "railway_project_id": os.getenv("RAILWAY_PROJECT_ID"),
        "railway_environment_id": os.getenv("RAILWAY_ENVIRONMENT_ID"),
        "railway_service_id": os.getenv("RAILWAY_SERVICE_ID"),
    }


def backfill_discovery(store) -> None:
    for i, task in enumerate(discovery_plan(), start=1):
        archive = monthly_kline("BTCUSDT", "5m", task.year, task.month)
        print(f"[BACKFILL {i:02d}/48] {archive.filename}", flush=True)

        # Always re-fetch checksum+archive before accepting an existing month.
        # This detects upstream source revisions instead of silently trusting cache.
        fetched = fetch_archive(archive)
        frame = normalize_5m(parse_usdm_kline_zip(fetched.payload))

        manifest = ingest_month_to_store(
            fetched=fetched,
            frame=frame,
            store=store,
            instrument_id="BTCUSDT-PERP.BINANCE",
            run_id=run_id(task.year, task.month),
        )
        print(
            "  status=",
            manifest.get("resume_status", "PUBLISHED"),
            "rows=",
            manifest["rows"],
            "source_sha=",
            manifest["actual_sha256"][:12],
            flush=True,
        )


def run_exp001(store_uri: str, bootstrap_reps: int) -> None:
    # Reuse the frozen research CLI implementation without duplicating logic.
    old_argv = sys.argv[:]
    try:
        sys.argv = [
            "exp001_remote",
            "--store-uri",
            store_uri,
            "--bootstrap-reps",
            str(bootstrap_reps),
        ]
        exp001_main()
    finally:
        sys.argv = old_argv


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--mode",
        choices=["smoke", "full-discovery"],
        default=os.getenv("QUANT_JOB_MODE", "smoke"),
    )
    p.add_argument(
        "--store-uri",
        default=None,
    )
    p.add_argument(
        "--bootstrap-reps",
        type=int,
        default=int(os.getenv("BOOTSTRAP_REPS", "500")),
    )
    args = p.parse_args()

    store_uri = resolve_store_uri(args.store_uri)
    store = open_store(store_uri)

    print("AI Quant Research Railway Worker")
    print("mode=", args.mode)
    print("store_uri=", store_uri)

    fingerprint_key = (
        "manifests/runtime/"
        f"runtime-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    write_bytes(
        store,
        fingerprint_key,
        json.dumps(environment_fingerprint(), indent=2).encode("utf-8"),
    )
    print("runtime fingerprint=", fingerprint_key)

    if args.mode == "smoke":
        print("SMOKE PASS: bucket write succeeded; no market backfill executed.")
        return

    backfill_discovery(store)

    snapshot_key = (
        "manifests/snapshots/DS-BTCUSDT-5M-DISCOVERY-v1.json"
    )
    if not exists(store, snapshot_key):
        key, snapshot = freeze_discovery_snapshot(store)
        print(
            "snapshot frozen=",
            key,
            snapshot["snapshot_sha256"],
            "rows=",
            snapshot["row_count"],
        )
    else:
        print("snapshot already exists; EXP-001 will verify and use it")

    run_exp001(store_uri, args.bootstrap_reps)
    print("FULL DISCOVERY PIPELINE COMPLETE")


if __name__ == "__main__":
    main()
