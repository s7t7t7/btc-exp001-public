import copy
import hashlib
import json

import numpy as np
import pandas as pd
import polars as pl
import pytest

from quant_platform.research import exp001_core as reference
from quant_platform.research import exp001_lowmem as optimized
from quant_platform.research import verified_snapshot as verified
from quant_platform.ingest.binance_data_vision import normalize_5m
from quant_platform.ingest.quality import audit_5m
from test_data_vision_headers import make_zip, ROW
from quant_platform.ingest.binance_data_vision import parse_usdm_kline_zip


def synthetic_frame():
    n = 60 * 288
    rng = np.random.default_rng(7601)
    ts = pd.date_range(end="2023-12-31 23:55:00", periods=n, freq="5min", tz="UTC")
    price = 30000 * np.exp(np.cumsum(rng.normal(0, .001, n)))
    opens = np.r_[price[0], price[:-1]]
    quote = np.exp(rng.normal(12, 1, n))
    ms = ts.as_unit("ms").asi8
    return pd.DataFrame({
        "source_open_time_ms": ms, "source_close_time_ms": ms + 299999,
        "available_at_ms": ms + 300000, "open": opens, "close": price,
        "high": np.maximum(opens, price) * 1.002,
        "low": np.minimum(opens, price) * .998, "quote_volume": quote,
        "taker_buy_quote_volume": quote * .5,
    })


def test_full_engine_equivalence_and_final_4h_boundary():
    frame = synthetic_frame()
    targets = reference.add_forward_targets(reference.build_features(frame))
    last_signal = len(frame) - 49
    assert targets.loc[last_signal, "open_time"] == pd.Timestamp("2023-12-31 19:55", tz="UTC")
    assert targets.loc[last_signal, "fwd_ret_4h"] == pytest.approx(
        frame.iloc[-1]["close"] / frame.iloc[last_signal + 1]["open"] - 1)
    expected = reference.run_discovery(targets, reps=20)
    actual = optimized.run_discovery_lowmem(frame, bootstrap_reps=20)
    assert actual["rows_scored"] == len(frame) - 48
    for name in ["raw", "adjusted"]:
        pd.testing.assert_frame_equal(actual[name][expected[name].columns], expected[name], rtol=1e-10, atol=1e-12)
    for name, result in expected["regimes"].items():
        pd.testing.assert_frame_equal(actual["regimes"][name][result.columns], result, rtol=1e-10, atol=1e-12)
    for family, metrics in expected["stats"].items():
        for metric, value in metrics.items():
            if isinstance(value, dict):
                for key in ["observed_uplift", "ci95_low", "ci95_high", "p_uplift_le_zero"]:
                    assert actual["stats"][family][metric][key] == pytest.approx(value[key], abs=1e-10)
            else:
                assert actual["stats"][family][metric] == pytest.approx(value)


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0.0, -1.0])
def test_data_gate_rejects_invalid_prices(bad):
    frame = normalize_5m(parse_usdm_kline_zip(make_zip(ROW + "\n")))
    frame = frame.with_columns(pl.lit(bad, dtype=pl.Float64).alias("close"))
    with pytest.raises(ValueError, match="Data Acceptance Gate"):
        audit_5m(frame)


def valid_snapshot():
    entries = []
    for y, m in verified.DISCOVERY_MONTHS:
        archive = verified.monthly_kline("BTCUSDT", "5m", y, m)
        start, end, rows = verified.month_bounds_ms(y, m)
        entries.append({"year": y, "month": m, "manifest_key": verified.ingest_manifest_key(y, m),
                        "canonical_key": verified.canonical_month_key(verified.INSTRUMENT, y, m, archive.filename),
                        "rows": rows, "min_open_time_ms": start, "max_open_time_ms": end})
    s = {"snapshot_id": verified.SNAPSHOT_ID, "status": "FROZEN", "phase": "discovery",
         "instrument_id": verified.INSTRUMENT, "interval": "5m", "row_count": 420768,
         "start": "2020-01-01T00:00:00Z", "end": "2023-12-31T23:55:00Z", "entries": entries}
    s["snapshot_sha256"] = hashlib.sha256(json.dumps(s, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return s


@pytest.mark.parametrize("change", ["oos", "missing_month", "path", "hash"])
def test_invalid_snapshot_rejected_before_any_data_read(monkeypatch, change):
    s = valid_snapshot()
    verified.validate_scope(s)
    s = copy.deepcopy(s)
    if change == "oos":
        s["phase"] = "oos"
    elif change == "missing_month":
        s["entries"].pop()
    elif change == "path":
        s["entries"][0]["canonical_key"] = "canonical/2025/oos.parquet"
    else:
        s["snapshot_sha256"] = "0" * 64
    def forbidden(*args):
        pytest.fail("Object read occurred before scope rejection")
    monkeypatch.setattr(verified, "read_bytes", forbidden)
    with pytest.raises(ValueError):
        verified.load_verified_discovery(None, s)


def test_modified_manifest_is_rejected(monkeypatch):
    s = valid_snapshot()
    for e in s["entries"]:
        e["manifest_sha256"] = "0" * 64
    s.pop("snapshot_sha256")
    s["snapshot_sha256"] = hashlib.sha256(json.dumps(s, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    monkeypatch.setattr(verified, "read_bytes", lambda *args: b"tampered")
    with pytest.raises(ValueError, match="manifest SHA256 mismatch"):
        verified.load_verified_discovery(None, s)


def test_snapshot_roundtrip_and_canonical_tampering(tmp_path, monkeypatch):
    from quant_platform.storage import StoreLocation, write_bytes
    from quant_platform.ingest.binance_data_vision import FetchResult
    from quant_platform.ingest.remote_publish import ingest_month_to_store
    from quant_platform.ingest.remote_snapshot import freeze_discovery_snapshot
    import pyarrow.fs as pafs

    store = StoreLocation(pafs.LocalFileSystem(), tmp_path.as_posix())
    payload = b"SYNTHETIC TEST SOURCE; not market evidence"
    sha = hashlib.sha256(payload).hexdigest()
    for y, m in verified.DISCOVERY_MONTHS:
        archive = verified.monthly_kline("BTCUSDT", "5m", y, m)
        start, end, rows = verified.month_bounds_ms(y, m)
        ts = np.arange(start, end + 300000, 300000, dtype=np.int64)
        frame = pl.DataFrame({"source_open_time_ms": ts, "source_close_time_ms": ts + 299999,
                              "available_at_ms": ts + 300000}).with_columns(
            *[pl.lit(100.0).alias(c) for c in ["open", "high", "low", "close"]],
            *[pl.lit(10.0).alias(c) for c in ["base_volume", "quote_volume"]],
            *[pl.lit(5.0).alias(c) for c in ["taker_buy_base_volume", "taker_buy_quote_volume"]],
            pl.lit(1, dtype=pl.Int64).alias("trade_count"))
        fetched = FetchResult(archive, payload, f"{sha}  {archive.filename}", sha, sha, "synthetic")
        ingest_month_to_store(fetched=fetched, frame=frame, store=store,
            instrument_id=verified.INSTRUMENT,
            run_id=f"INGEST-BINANCE-BTCUSDT-5M-{y:04d}-{m:02d}-v1")
    _, snapshot = freeze_discovery_snapshot(store)
    assert verified.load_verified_discovery(store, snapshot).num_rows == 420768
    write_bytes(store, snapshot["entries"][0]["canonical_key"], b"tampered", overwrite=True)
    with pytest.raises(ValueError, match="Canonical Parquet SHA256 mismatch"):
        verified.load_verified_discovery(store, snapshot)
