from __future__ import annotations

from datetime import datetime,timezone
import hashlib,json

from quant_platform.storage import exists,read_bytes,write_bytes


DISCOVERY_MONTHS=[(y,m) for y in range(2020,2024) for m in range(1,13)]


def ingest_manifest_key(year,month):
    return (
        "manifests/ingest/"
        f"INGEST-BINANCE-BTCUSDT-5M-{year:04d}-{month:02d}-v1.json"
    )


def freeze_discovery_snapshot(store, snapshot_id="DS-BTCUSDT-5M-DISCOVERY-v1"):
    entries=[]
    previous_max=None

    for year,month in DISCOVERY_MONTHS:
        key=ingest_manifest_key(year,month)
        if not exists(store,key):
            raise RuntimeError(f"Missing ingest manifest: {key}")
        raw=read_bytes(store,key)
        obj=json.loads(raw)

        if obj.get("status")!="VALID":
            raise RuntimeError(f"Non-VALID month in snapshot: {key}")
        if int(obj.get("duplicates",0))!=0 or int(obj.get("gaps",0))!=0:
            raise RuntimeError(f"Month has gaps/duplicates: {key}")

        objects=obj["objects"]
        for object_key in objects.values():
            if not exists(store,object_key):
                raise RuntimeError(f"Manifest references missing object: {object_key}")

        mn=int(obj["min_open_time_ms"])
        mx=int(obj["max_open_time_ms"])
        if previous_max is not None and mn-previous_max != 300_000:
            raise RuntimeError(
                f"Month boundary discontinuity before {year:04d}-{month:02d}: "
                f"{previous_max} -> {mn}"
            )
        previous_max=mx

        entries.append({
            "year":year,
            "month":month,
            "manifest_key":key,
            "manifest_sha256":hashlib.sha256(raw).hexdigest(),
            "source_sha256":obj["actual_sha256"],
            "canonical_sha256":obj["canonical_sha256"],
            "canonical_key":objects["canonical_parquet"],
            "rows":obj["rows"],
            "min_open_time_ms":mn,
            "max_open_time_ms":mx,
        })

    snapshot={
        "snapshot_id":snapshot_id,
        "status":"FROZEN",
        "instrument_id":"BTCUSDT-PERP.BINANCE",
        "interval":"5m",
        "phase":"discovery",
        "start":"2020-01-01T00:00:00Z",
        "end":"2023-12-31T23:55:00Z",
        "entries":entries,
        "row_count":sum(int(x["rows"]) for x in entries),
        "created_at":datetime.now(timezone.utc).isoformat(),
    }
    canonical=json.dumps(snapshot,sort_keys=True,separators=(",",":")).encode()
    snapshot["snapshot_sha256"]=hashlib.sha256(canonical).hexdigest()

    out=f"manifests/snapshots/{snapshot_id}.json"
    write_bytes(
        store,out,json.dumps(snapshot,sort_keys=True,indent=2).encode("utf-8")
    )
    return out,snapshot
