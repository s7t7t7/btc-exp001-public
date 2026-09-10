from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

from quant_platform.storage import (
    exists,
    parquet_bytes_from_polars,
    read_bytes,
    sha256_object,
    write_bytes,
)
from quant_platform.ingest.quality import audit_5m, BAR_MS


def month_bounds_ms(year: int, month: int):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        nxt = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(year, month + 1, 1, tzinfo=timezone.utc)

    start_ms = int(start.timestamp() * 1000)
    next_ms = int(nxt.timestamp() * 1000)
    return start_ms, next_ms - BAR_MS, int((next_ms - start_ms) // BAR_MS)


def raw_archive_keys(archive):
    base = (
        f"raw/binance/usdm/klines/{archive.symbol}/{archive.interval}/"
        f"year={archive.year:04d}/month={archive.month:02d}"
    )
    return f"{base}/{archive.filename}", f"{base}/{archive.filename}.CHECKSUM"


def canonical_month_key(instrument_id, year, month, archive_filename):
    return (
        "canonical/binance_usdm_5m/"
        f"instrument={instrument_id}/year={year:04d}/month={month:02d}/"
        f"{archive_filename.removesuffix('.zip')}.parquet"
    )


def manifest_key(run_id):
    return f"manifests/ingest/{run_id}.json"


def _canonical_manifest_payload(
    *,
    run_id,
    fetched,
    canonical_sha256,
    instrument_id,
    audit,
    raw_zip_key,
    raw_checksum_key,
    canonical_key,
    expected_rows,
):
    archive = fetched.archive
    return {
        "ingest_run_id": run_id,
        "status": "VALID",
        "source": "BINANCE_DATA_VISION",
        "source_url": archive.url,
        "checksum_url": archive.checksum_url,
        "archive_filename": archive.filename,
        "official_sha256": fetched.official_sha256,
        "actual_sha256": fetched.actual_sha256,
        "canonical_sha256": canonical_sha256,
        "instrument_id": instrument_id,
        "symbol": archive.symbol,
        "interval": archive.interval,
        "year": archive.year,
        "month": archive.month,
        "schema_version": "CANONICAL-BINANCE-5M-v1",
        "available_at_rule": "source_close_time_ms + 1",
        "expected_rows": expected_rows,
        "rows": audit.rows,
        "duplicates": audit.duplicate_timestamps,
        "gaps": audit.missing_grid_bars,
        "open_grid_mismatch": audit.open_grid_mismatch,
        "close_time_mismatch": audit.close_time_mismatch,
        "boundary_mismatch": audit.boundary_mismatch,
        "expected_rows_mismatch": audit.expected_rows_mismatch,
        "min_open_time_ms": audit.min_open_time_ms,
        "max_open_time_ms": audit.max_open_time_ms,
        "objects": {
            "raw_zip": raw_zip_key,
            "raw_checksum": raw_checksum_key,
            "canonical_parquet": canonical_key,
        },
    }


def _verify_existing_valid_manifest(
    store,
    key: str,
    expected: dict,
    raw_checksum_sha256: str,
) -> dict:
    obj = json.loads(read_bytes(store, key))
    if obj.get("status") != "VALID":
        raise RuntimeError(f"Existing manifest is not VALID: {key}")

    immutable_fields = (
        "actual_sha256",
        "canonical_sha256",
        "instrument_id",
        "year",
        "month",
        "rows",
        "min_open_time_ms",
        "max_open_time_ms",
    )
    mismatches = {
        field: (obj.get(field), expected.get(field))
        for field in immutable_fields
        if obj.get(field) != expected.get(field)
    }
    if mismatches:
        raise RuntimeError(
            f"Existing manifest conflicts with current verified source: {mismatches}"
        )

    targets = obj["objects"]
    for object_key in targets.values():
        if not exists(store, object_key):
            raise RuntimeError(
                f"Existing VALID manifest references missing object: {object_key}"
            )

    if sha256_object(store, targets["raw_zip"]) != expected["actual_sha256"]:
        raise RuntimeError("Existing RAW ZIP hash does not match VALID manifest")
    if sha256_object(store, targets["raw_checksum"]) != raw_checksum_sha256:
        raise RuntimeError("Existing CHECKSUM object hash mismatch")
    if sha256_object(store, targets["canonical_parquet"]) != expected["canonical_sha256"]:
        raise RuntimeError("Existing canonical Parquet hash mismatch")

    obj["resume_status"] = "SKIPPED_EXISTING_VERIFIED"
    return obj


def _write_or_verify(
    store,
    key: str,
    payload: bytes,
    expected_sha256: str,
):
    if exists(store, key):
        actual = sha256_object(store, key)
        if actual != expected_sha256:
            raise RuntimeError(
                f"Orphan object conflict for {key}: "
                f"existing={actual}, expected={expected_sha256}"
            )
        return "VERIFIED_EXISTING"

    write_bytes(store, key, payload)
    return "WRITTEN"


def ingest_month_to_store(
    *,
    fetched,
    frame,
    store,
    instrument_id,
    run_id,
):
    archive = fetched.archive
    exp_start, exp_end, exp_rows = month_bounds_ms(archive.year, archive.month)
    audit = audit_5m(
        frame,
        expected_start_ms=exp_start,
        expected_end_ms=exp_end,
        expected_rows=exp_rows,
    )
    if not audit.passed:
        raise RuntimeError(
            "Data Acceptance Gate failed; canonical publish forbidden: "
            + json.dumps(audit.to_dict(), sort_keys=True)
        )

    raw_zip_key, raw_checksum_key = raw_archive_keys(archive)
    canonical_key = canonical_month_key(
        instrument_id,
        archive.year,
        archive.month,
        archive.filename,
    )
    mkey = manifest_key(run_id)

    checksum_payload = fetched.checksum_text.encode("utf-8")
    checksum_sha = hashlib.sha256(checksum_payload).hexdigest()

    canonical_payload = parquet_bytes_from_polars(frame)
    canonical_sha = hashlib.sha256(canonical_payload).hexdigest()

    base_manifest = _canonical_manifest_payload(
        run_id=run_id,
        fetched=fetched,
        canonical_sha256=canonical_sha,
        instrument_id=instrument_id,
        audit=audit,
        raw_zip_key=raw_zip_key,
        raw_checksum_key=raw_checksum_key,
        canonical_key=canonical_key,
        expected_rows=exp_rows,
    )

    # Fully completed month: verify and skip safely.
    if exists(store, mkey):
        return _verify_existing_valid_manifest(
            store,
            mkey,
            base_manifest,
            checksum_sha,
        )

    # Interrupted previous publish: objects may exist without the final manifest.
    # Matching hashes are safe to reuse; mismatching bytes stop the pipeline.
    object_status = {
        "raw_zip": _write_or_verify(
            store,
            raw_zip_key,
            fetched.payload,
            fetched.actual_sha256,
        ),
        "raw_checksum": _write_or_verify(
            store,
            raw_checksum_key,
            checksum_payload,
            checksum_sha,
        ),
        "canonical_parquet": _write_or_verify(
            store,
            canonical_key,
            canonical_payload,
            canonical_sha,
        ),
    }

    manifest = {
        **base_manifest,
        "object_publish_status": object_status,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }
    hash_basis = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest["manifest_content_sha256"] = hashlib.sha256(hash_basis).hexdigest()

    # Commit marker written LAST.
    write_bytes(
        store,
        mkey,
        json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8"),
    )
    return manifest
