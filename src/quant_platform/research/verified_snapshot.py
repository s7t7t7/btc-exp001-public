"""Validate Discovery scope and exact object bytes before research reads labels."""
import hashlib
import json

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

from quant_platform.ingest.binance_data_vision import monthly_kline, parse_checksum
from quant_platform.ingest.quality import audit_5m
from quant_platform.ingest.remote_publish import month_bounds_ms, raw_archive_keys, canonical_month_key
from quant_platform.ingest.remote_snapshot import DISCOVERY_MONTHS, ingest_manifest_key
from quant_platform.storage import read_bytes

INSTRUMENT = "BTCUSDT-PERP.BINANCE"
SNAPSHOT_ID = "DS-BTCUSDT-5M-DISCOVERY-v1"


def verify_json_hash(obj, field):
    basis = {k: v for k, v in obj.items() if k != field}
    actual = hashlib.sha256(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if obj.get(field) != actual:
        raise ValueError(f"Invalid {field}")


def validate_scope(snapshot):
    expected = {
        "snapshot_id": SNAPSHOT_ID, "status": "FROZEN", "phase": "discovery",
        "instrument_id": INSTRUMENT, "interval": "5m", "row_count": 420768,
        "start": "2020-01-01T00:00:00Z", "end": "2023-12-31T23:55:00Z",
    }
    if any(snapshot.get(k) != v for k, v in expected.items()):
        raise ValueError("Discovery scope mismatch; Validation/OOS are locked")
    entries = snapshot.get("entries", [])
    if [(e.get("year"), e.get("month")) for e in entries] != DISCOVERY_MONTHS:
        raise ValueError("Discovery requires exactly 48 ordered months")
    for e in entries:
        y, m = e["year"], e["month"]
        archive = monthly_kline("BTCUSDT", "5m", y, m)
        start, end, rows = month_bounds_ms(y, m)
        expected_entry = {
            "manifest_key": ingest_manifest_key(y, m),
            "canonical_key": canonical_month_key(INSTRUMENT, y, m, archive.filename),
            "rows": rows, "min_open_time_ms": start, "max_open_time_ms": end,
        }
        if any(e.get(k) != v for k, v in expected_entry.items()):
            raise ValueError("Discovery entry scope mismatch")
    verify_json_hash(snapshot, "snapshot_sha256")


def load_verified_discovery(store, snapshot):
    validate_scope(snapshot)  # No object may be read before the full scope check.
    tables = []
    for e in snapshot["entries"]:
        raw = read_bytes(store, e["manifest_key"])
        if hashlib.sha256(raw).hexdigest() != e["manifest_sha256"]:
            raise ValueError("Monthly manifest SHA256 mismatch")
        manifest = json.loads(raw)
        verify_json_hash(manifest, "manifest_content_sha256")
        archive = monthly_kline("BTCUSDT", "5m", e["year"], e["month"])
        zip_key, checksum_key = raw_archive_keys(archive)
        expected_objects = {"raw_zip": zip_key, "raw_checksum": checksum_key,
                            "canonical_parquet": e["canonical_key"]}
        checks = {"status": "VALID", "instrument_id": INSTRUMENT,
                  "symbol": "BTCUSDT", "interval": "5m", "year": e["year"],
                  "month": e["month"], "rows": e["rows"], "expected_rows": e["rows"],
                  "actual_sha256": e["source_sha256"], "official_sha256": e["source_sha256"],
                  "canonical_sha256": e["canonical_sha256"], "objects": expected_objects,
                  "min_open_time_ms": e["min_open_time_ms"],
                  "max_open_time_ms": e["max_open_time_ms"]}
        if any(manifest.get(k) != v for k, v in checks.items()):
            raise ValueError("Monthly manifest disagrees with Discovery snapshot")
        if parse_checksum(read_bytes(store, checksum_key).decode(), archive.filename) != e["source_sha256"]:
            raise ValueError("Official source checksum mismatch")
        if hashlib.sha256(read_bytes(store, zip_key)).hexdigest() != e["source_sha256"]:
            raise ValueError("Source archive SHA256 mismatch")
        payload = read_bytes(store, e["canonical_key"])
        if hashlib.sha256(payload).hexdigest() != e["canonical_sha256"]:
            raise ValueError("Canonical Parquet SHA256 mismatch")
        table = pq.read_table(pa.BufferReader(payload))
        audit = audit_5m(pl.from_arrow(table), expected_start_ms=e["min_open_time_ms"],
                         expected_end_ms=e["max_open_time_ms"], expected_rows=e["rows"])
        if not audit.passed:
            raise ValueError(f"Snapshot Data Acceptance Gate failed: {audit.to_dict()}")
        tables.append(table)
    return pa.concat_tables(tables)
