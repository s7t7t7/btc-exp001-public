from __future__ import annotations

import argparse
import io
import json
import hashlib

import pandas as pd

from quant_platform.storage import open_store,read_bytes,read_parquet_table,write_bytes
from quant_platform.research.exp001_lowmem import run_discovery_lowmem
from quant_platform.research.verified_snapshot import load_verified_discovery


PROTOCOL_ID="EXP-001-v1.1"
SNAPSHOT_KEY="manifests/snapshots/DS-BTCUSDT-5M-DISCOVERY-v1.json"


def _csv_bytes(df):
    return df.to_csv(index=False).encode("utf-8")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--store-uri",required=True)
    p.add_argument("--bootstrap-reps",type=int,default=500)
    a=p.parse_args()
    if a.bootstrap_reps != 500:
        p.error("Frozen Discovery requires exactly 500 bootstrap repetitions")

    store=open_store(a.store_uri)
    snapshot_raw=read_bytes(store,SNAPSHOT_KEY)
    snapshot=json.loads(snapshot_raw)
    if snapshot.get("status")!="FROZEN" or snapshot.get("phase")!="discovery":
        raise RuntimeError("EXP-001 Discovery requires a FROZEN Discovery snapshot")

    table=load_verified_discovery(store, snapshot)
    df=table.to_pandas(split_blocks=True, self_destruct=True)
    df=df.sort_values("source_open_time_ms").reset_index(drop=True)

    # Snapshot-level semantic guards before any feature calculation.
    if df["source_open_time_ms"].duplicated().any():
        raise RuntimeError("Snapshot has duplicate timestamps")
    diffs=df["source_open_time_ms"].diff().dropna()
    if (diffs!=300_000).any():
        raise RuntimeError("Snapshot is not continuous 5m data")
    if not (
        df["available_at_ms"].to_numpy()
        == (df["source_close_time_ms"].to_numpy()+1)
    ).all():
        raise RuntimeError("Availability-time invariant failed")

    result=run_discovery_lowmem(
        df,
        bootstrap_reps=a.bootstrap_reps,
    )

    base=(
        "research/results/EXP-001/v1.1/discovery/"
        f"{snapshot['snapshot_id']}"
    )
    outputs={
        f"{base}/EXP-001A_raw_volume_buckets.csv":
            _csv_bytes(result["raw"]),
        f"{base}/EXP-001B_adjusted_volume_buckets.csv":
            _csv_bytes(result["adjusted"]),
        f"{base}/stats.json":
            json.dumps(result["stats"],indent=2,sort_keys=True,default=float).encode(),
    }
    for name,frame in result["regimes"].items():
        outputs[f"{base}/regime_{name}.csv"]=_csv_bytes(frame)

    evidence={
        "experiment_id":"EXP-001",
        "protocol_version":"v1.1",
        "phase":"discovery",
        "hypothesis_id":"HYP-001",
        "snapshot_id":snapshot["snapshot_id"],
        "snapshot_sha256":snapshot["snapshot_sha256"],
        "snapshot_object_sha256":hashlib.sha256(snapshot_raw).hexdigest(),
        "bootstrap_reps":a.bootstrap_reps,
        "rows_scored":result["rows_scored"],
        "status":"COMPUTED_NOT_YET_REVIEWED",
        "stats":result["stats"],
    }
    outputs[f"{base}/evidence.json"]=json.dumps(
        evidence,indent=2,sort_keys=True,default=float
    ).encode()

    for key,payload in outputs.items():
        write_bytes(store,key,payload,overwrite=False)

    print(
        "EXP-001 DISCOVERY COMPUTED",
        "snapshot=",snapshot["snapshot_id"],
        "rows=",result["rows_scored"],
        "output=",base,
    )

if __name__=="__main__":
    main()
