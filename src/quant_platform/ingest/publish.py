from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json, shutil, hashlib
from quant_platform.ingest.quality import audit_5m

def publish_validated_month(
    df, *, instrument_id, source_filename, source_sha256,
    canonical_root:Path, manifest_root:Path
):
    audit=audit_5m(df)
    if not audit.passed:
        raise RuntimeError("Data Acceptance Gate failed; publish forbidden")
    if not audit.min_open_time_ms:
        raise RuntimeError("Empty dataset")
    dt=datetime.fromtimestamp(audit.min_open_time_ms/1000, tz=timezone.utc)
    outdir=(canonical_root/"binance_usdm_5m"/
            f"instrument={instrument_id}"/f"year={dt.year:04d}"/
            f"month={dt.month:02d}")
    outdir.mkdir(parents=True, exist_ok=True)
    target=outdir/(source_filename.replace(".zip",".parquet"))
    if target.exists():
        raise FileExistsError("Silent overwrite forbidden; use repair/supersede flow")
    df.write_parquet(target, compression="zstd")
    file_sha=hashlib.sha256(target.read_bytes()).hexdigest()
    manifest={
        "source":"BINANCE_DATA_VISION",
        "source_filename":source_filename,
        "source_sha256":source_sha256,
        "canonical_file":str(target),
        "canonical_sha256":file_sha,
        "instrument_id":instrument_id,
        "schema_version":"CANONICAL-BINANCE-5M-v1",
        "rows":audit.rows,
        "gaps":audit.missing_grid_bars,
        "duplicates":audit.duplicate_timestamps,
        "min_open_time_ms":audit.min_open_time_ms,
        "max_open_time_ms":audit.max_open_time_ms,
        "status":"VALID",
        "published_at":datetime.now(timezone.utc).isoformat(),
    }
    manifest_root.mkdir(parents=True, exist_ok=True)
    mp=manifest_root/(source_filename.replace(".zip",".manifest.json"))
    if mp.exists():
        raise FileExistsError("Manifest overwrite forbidden")
    mp.write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return target, mp, manifest
