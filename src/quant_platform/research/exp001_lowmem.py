from __future__ import annotations

import math
import gc

import numpy as np
import pandas as pd
from pandas.api.indexers import FixedForwardWindowIndexer


HORIZONS = {"5m": 1, "15m": 3, "30m": 6, "1h": 12, "4h": 48}
BUCKETS = [
    "00-10","10-20","20-30","30-40","40-50","50-60",
    "60-70","70-80","80-90","90-95","95-99","99+",
]
QS = [.10,.20,.30,.40,.50,.60,.70,.80,.90,.95,.99]
W = 30 * 288
MINP = 7 * 288
SAME_SLOT_DAYS = 30
SAME_SLOT_MIN = 14
FUNDING_CENTERS = (0, 480, 960)
FUNDING_HALF_WINDOW = 30


def _funding_window(ts: pd.Series) -> np.ndarray:
    mins = (ts.dt.hour * 60 + ts.dt.minute).to_numpy()
    out = np.zeros(len(ts), dtype=bool)
    for center in FUNDING_CENTERS:
        d = np.abs(mins - center)
        d = np.minimum(d, 1440 - d)
        out |= d <= FUNDING_HALF_WINDOW
    return out


def _causal_bucket_score(values: pd.Series) -> np.ndarray:
    """
    Exact percentile-bucket semantics of EXP-001 v1.1, but does not retain
    eleven rolling-quantile columns. -1 means insufficient causal history.
    """
    past = values.shift(1)
    roll = past.rolling(W, min_periods=MINP)
    valid = roll.count().to_numpy() >= MINP
    value_arr = values.to_numpy(dtype=float, copy=False)

    score = np.full(len(values), len(QS), dtype=np.int8)
    unresolved = valid & np.isfinite(value_arr)

    for i, q in enumerate(QS):
        threshold = roll.quantile(q).to_numpy(dtype=float, copy=False)
        mask = unresolved & (value_arr < threshold)
        score[mask] = i
        unresolved &= ~mask
        del threshold, mask

    score[~valid] = -1
    return score


def _same_slot_causal_median(
    values: pd.Series,
    slot_of_day: pd.Series,
) -> pd.Series:
    tmp = pd.DataFrame(
        {"v": values.to_numpy(copy=False), "slot": slot_of_day.to_numpy(copy=False)}
    )
    out = (
        tmp.groupby("slot", sort=False)["v"]
        .transform(
            lambda s: s.shift(1).rolling(
                SAME_SLOT_DAYS,
                min_periods=SAME_SLOT_MIN,
            ).median()
        )
    )
    return out


def build_base(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only columns EXP-001 actually needs. RC-001 source data remains intact
    in canonical Parquet; this is only a low-memory research view.
    """
    cols = [
        "source_open_time_ms",
        "source_close_time_ms",
        "available_at_ms",
        "open","high","low","close","quote_volume",
    ]
    x = df[cols].copy()
    x.sort_values("source_open_time_ms", inplace=True)
    x.reset_index(drop=True, inplace=True)

    x["open_time"] = pd.to_datetime(
        x["source_open_time_ms"],
        unit="ms",
        utc=True,
    )
    x["slot"] = (
        x["open_time"].dt.hour * 12 + x["open_time"].dt.minute // 5
    ).astype("int16")
    x["is_weekend"] = (x["open_time"].dt.weekday >= 5).to_numpy()
    x["is_funding_window"] = _funding_window(x["open_time"])

    log_volume = pd.Series(
        np.log1p(x["quote_volume"].to_numpy(dtype=float, copy=False)),
        index=x.index,
    )
    x["raw_score"] = _causal_bucket_score(log_volume)

    expected_log_volume = _same_slot_causal_median(log_volume, x["slot"])
    volume_surprise = log_volume - expected_log_volume
    x["surprise_score"] = _causal_bucket_score(volume_surprise)

    # These large intermediate Series are intentionally not stored.
    del log_volume, expected_log_volume, volume_surprise
    gc.collect()
    return x


def _bucket_mean(values: np.ndarray, scores: np.ndarray) -> list[float]:
    out = []
    for score in range(len(BUCKETS)):
        mask = scores == score
        out.append(float(np.nanmean(values[mask])) if mask.any() else np.nan)
    return out


def _bucket_counts(scores: np.ndarray) -> list[int]:
    return [int(np.sum(scores == score)) for score in range(len(BUCKETS))]


def _spearman_order(values: list[float]) -> float:
    s = pd.Series(values, dtype=float)
    valid = s.notna()
    if valid.sum() < 3:
        return np.nan
    x = pd.Series(np.arange(len(BUCKETS), dtype=float))[valid]
    y = s[valid].rank(method="average")
    return float(x.corr(y))


def _fast_moving_block_bootstrap(
    metric: np.ndarray,
    score: np.ndarray,
    *,
    reps: int = 500,
    block: int = 288,
    seed: int = 20260910,
) -> dict:
    """
    Algebraically equivalent to the original moving-block resampling:
    sample block starts uniformly, concatenate one-day blocks, truncate the
    last sampled block to n observations, then compute high90-vs-under80 uplift.

    Uses cumulative block sums/counts instead of allocating an n-length index
    vector for every bootstrap repetition.
    """
    valid = np.isfinite(metric) & (score >= 0)
    vals = np.asarray(metric[valid], dtype=np.float64)
    sc = np.asarray(score[valid], dtype=np.int8)
    n = len(vals)

    if n < block * 4:
        return {"error": "insufficient_rows", "n": int(n)}

    hi = sc >= 9       # 90-95, 95-99, 99+
    base = sc < 8      # below 80th percentile

    def observed_uplift():
        return float(vals[hi].mean() - vals[base].mean())

    hi_sum = np.where(hi, vals, 0.0)
    base_sum = np.where(base, vals, 0.0)
    hi_count = hi.astype(np.int64)
    base_count = base.astype(np.int64)

    def prefix(a):
        return np.concatenate(([0], np.cumsum(a)))

    phs = prefix(hi_sum)
    pbs = prefix(base_sum)
    phc = prefix(hi_count)
    pbc = prefix(base_count)

    def window(prefix_arr, starts, length):
        return prefix_arr[starts + length] - prefix_arr[starts]

    n_blocks = math.ceil(n / block)
    residual = n - block * (n_blocks - 1)
    max_start = n - block
    rng = np.random.default_rng(seed)
    diffs = np.empty(reps, dtype=np.float64)

    for r in range(reps):
        starts = rng.integers(0, max_start + 1, size=n_blocks)

        full = starts[:-1]
        last = starts[-1]

        hs = window(phs, full, block).sum() if len(full) else 0.0
        bs = window(pbs, full, block).sum() if len(full) else 0.0
        hc = window(phc, full, block).sum() if len(full) else 0
        bc = window(pbc, full, block).sum() if len(full) else 0

        hs += window(phs, np.asarray([last]), residual)[0]
        bs += window(pbs, np.asarray([last]), residual)[0]
        hc += window(phc, np.asarray([last]), residual)[0]
        bc += window(pbc, np.asarray([last]), residual)[0]

        diffs[r] = (hs / hc) - (bs / bc)

    return {
        "observed_uplift": observed_uplift(),
        "bootstrap_reps": int(reps),
        "ci95_low": float(np.quantile(diffs, .025)),
        "ci95_high": float(np.quantile(diffs, .975)),
        "p_uplift_le_zero": float(np.mean(diffs <= 0)),
        "n": int(n),
    }


def _init_summary(scores: np.ndarray) -> list[dict]:
    counts = _bucket_counts(scores)
    return [
        {"bucket": BUCKETS[i], "n": counts[i]}
        for i in range(len(BUCKETS))
    ]


def _fill(summary: list[dict], prefix: str, values: np.ndarray):
    means = _bucket_mean(values, np.asarray(
        [BUCKETS.index(row["bucket"]) for row in summary], dtype=np.int8
    ))
    # This helper is intentionally unused; actual score-aware fill below.
    return means


def _put_bucket_metric(
    summary: list[dict],
    scores: np.ndarray,
    column: str,
    values: np.ndarray,
):
    means = _bucket_mean(values, scores)
    for i, value in enumerate(means):
        summary[i][column] = value


def run_discovery_lowmem(
    df: pd.DataFrame,
    *,
    bootstrap_reps: int = 500,
) -> dict:
    x = build_base(df)

    # Purge maximum 4h horizon at frozen Discovery right boundary.
    cutoff = pd.Timestamp("2023-12-31 19:55:00", tz="UTC")
    score_mask = x["open_time"] <= cutoff
    # Keep the final four hours as label observations, never as scored signals.
    scored = x.loc[score_mask]

    raw_score = scored["raw_score"].to_numpy(dtype=np.int8, copy=False)
    surprise_score = scored["surprise_score"].to_numpy(dtype=np.int8, copy=False)
    weekend = scored["is_weekend"].to_numpy(dtype=bool, copy=False)
    funding = scored["is_funding_window"].to_numpy(dtype=bool, copy=False)

    raw_summary = _init_summary(raw_score)
    adjusted_summary = _init_summary(surprise_score)
    regime_summaries = {
        "weekday": _init_summary(np.where(~weekend, surprise_score, -1)),
        "weekend": _init_summary(np.where(weekend, surprise_score, -1)),
        "funding_window": _init_summary(np.where(funding, surprise_score, -1)),
        "non_funding": _init_summary(np.where(~funding, surprise_score, -1)),
    }

    stats = {
        "EXP-001A_raw_monotonicity": {},
        "EXP-001B_adjusted_monotonicity": {},
        "EXP-001A_raw_bootstrap": {},
        "EXP-001B_adjusted_bootstrap": {},
    }

    open_arr = x["open"].to_numpy(dtype=np.float64, copy=False)
    high_arr = x["high"].to_numpy(dtype=np.float64, copy=False)
    low_arr = x["low"].to_numpy(dtype=np.float64, copy=False)
    close_arr = x["close"].to_numpy(dtype=np.float64, copy=False)
    slot = x["slot"]

    # Calculate labels on the full Discovery frame, then apply the score mask.
    logret = pd.Series(np.log(close_arr / np.r_[np.nan, close_arr[:-1]]))

    for label, h in HORIZONS.items():
        entry = pd.Series(open_arr).shift(-1)
        signed = pd.Series(close_arr).shift(-h) / entry - 1.0
        abs_ret = signed.abs()

        fw = FixedForwardWindowIndexer(window_size=h)
        future_high = (
            pd.Series(high_arr).shift(-1).rolling(fw, min_periods=h).max()
        )
        future_low = (
            pd.Series(low_arr).shift(-1).rolling(fw, min_periods=h).min()
        )
        mfe = future_high / entry - 1.0
        mae_abs = -(future_low / entry - 1.0)

        future_sq = (
            logret.shift(-1).pow(2).rolling(fw, min_periods=h).sum()
        )
        rv = np.sqrt(future_sq)

        exp_rv = _same_slot_causal_median(rv, slot)
        exp_abs = _same_slot_causal_median(abs_ret, slot)
        excess_rv = rv / exp_rv.replace(0, np.nan) - 1.0
        excess_abs = abs_ret / exp_abs.replace(0, np.nan) - 1.0

        vals = {
            f"signed_ret_{label}": signed.to_numpy(dtype=float),
            f"abs_ret_{label}": abs_ret.to_numpy(dtype=float),
            f"rv_{label}": np.asarray(rv, dtype=float),
            f"mfe_{label}": mfe.to_numpy(dtype=float),
            f"mae_abs_{label}": mae_abs.to_numpy(dtype=float),
            f"excess_rv_ratio_{label}": excess_rv.to_numpy(dtype=float),
            f"excess_abs_ret_ratio_{label}": excess_abs.to_numpy(dtype=float),
        }
        vals = {key: value[score_mask.to_numpy()] for key, value in vals.items()}

        for col in (
            f"signed_ret_{label}",
            f"abs_ret_{label}",
            f"rv_{label}",
            f"mfe_{label}",
            f"mae_abs_{label}",
        ):
            _put_bucket_metric(raw_summary, raw_score, col, vals[col])
            _put_bucket_metric(adjusted_summary, surprise_score, col, vals[col])

        for col in (
            f"excess_rv_ratio_{label}",
            f"excess_abs_ret_ratio_{label}",
        ):
            _put_bucket_metric(adjusted_summary, surprise_score, col, vals[col])

        for regime, mask in {
            "weekday": ~weekend,
            "weekend": weekend,
            "funding_window": funding,
            "non_funding": ~funding,
        }.items():
            sc = np.where(mask, surprise_score, -1)
            for col in (
                f"signed_ret_{label}",
                f"abs_ret_{label}",
                f"rv_{label}",
                f"mfe_{label}",
                f"mae_abs_{label}",
                f"excess_rv_ratio_{label}",
                f"excess_abs_ret_ratio_{label}",
            ):
                _put_bucket_metric(regime_summaries[regime], sc, col, vals[col])

        raw_abs_means = [row[f"abs_ret_{label}"] for row in raw_summary]
        raw_rv_means = [row[f"rv_{label}"] for row in raw_summary]
        adj_abs_means = [
            row[f"excess_abs_ret_ratio_{label}"] for row in adjusted_summary
        ]
        adj_rv_means = [
            row[f"excess_rv_ratio_{label}"] for row in adjusted_summary
        ]

        stats["EXP-001A_raw_monotonicity"][f"abs_ret_{label}"] = (
            _spearman_order(raw_abs_means)
        )
        stats["EXP-001A_raw_monotonicity"][f"rv_{label}"] = (
            _spearman_order(raw_rv_means)
        )
        stats["EXP-001B_adjusted_monotonicity"][
            f"excess_abs_ret_ratio_{label}"
        ] = _spearman_order(adj_abs_means)
        stats["EXP-001B_adjusted_monotonicity"][
            f"excess_rv_ratio_{label}"
        ] = _spearman_order(adj_rv_means)

        stats["EXP-001A_raw_bootstrap"][f"abs_ret_{label}"] = (
            _fast_moving_block_bootstrap(
                vals[f"abs_ret_{label}"],
                raw_score,
                reps=bootstrap_reps,
            )
        )
        stats["EXP-001A_raw_bootstrap"][f"rv_{label}"] = (
            _fast_moving_block_bootstrap(
                vals[f"rv_{label}"],
                raw_score,
                reps=bootstrap_reps,
            )
        )
        stats["EXP-001B_adjusted_bootstrap"][
            f"excess_abs_ret_ratio_{label}"
        ] = _fast_moving_block_bootstrap(
            vals[f"excess_abs_ret_ratio_{label}"],
            surprise_score,
            reps=bootstrap_reps,
        )
        stats["EXP-001B_adjusted_bootstrap"][
            f"excess_rv_ratio_{label}"
        ] = _fast_moving_block_bootstrap(
            vals[f"excess_rv_ratio_{label}"],
            surprise_score,
            reps=bootstrap_reps,
        )

        del entry, signed, abs_ret, future_high, future_low, mfe, mae_abs
        del future_sq, rv, exp_rv, exp_abs, excess_rv, excess_abs, vals
        gc.collect()

    return {
        "rows_scored": int(len(scored)),
        "raw": pd.DataFrame(raw_summary),
        "adjusted": pd.DataFrame(adjusted_summary),
        "regimes": {
            k: pd.DataFrame(v) for k, v in regime_summaries.items()
        },
        "stats": stats,
    }
