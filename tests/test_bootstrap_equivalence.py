import math
import numpy as np

from quant_platform.research.exp001_lowmem import _fast_moving_block_bootstrap


def slow_boot(metric, score, reps, block, seed):
    valid = np.isfinite(metric) & (score >= 0)
    vals = metric[valid]
    sc = score[valid]
    n = len(vals)
    rng = np.random.default_rng(seed)
    max_start = n - block
    n_blocks = math.ceil(n / block)
    diffs = []
    for _ in range(reps):
        starts = rng.integers(0, max_start + 1, size=n_blocks)
        idx = np.concatenate(
            [np.arange(s, s + block) for s in starts]
        )[:n]
        v = vals[idx]
        q = sc[idx]
        diffs.append(v[q >= 9].mean() - v[q < 8].mean())
    return np.asarray(diffs)


def test_fast_bootstrap_matches_original_resampling():
    rng = np.random.default_rng(99)
    n = 2500
    metric = rng.normal(size=n)
    score = rng.integers(0, 12, size=n, dtype=np.int8)
    reps = 50
    block = 288
    seed = 20260910

    slow = slow_boot(metric, score, reps, block, seed)
    fast = _fast_moving_block_bootstrap(
        metric,
        score,
        reps=reps,
        block=block,
        seed=seed,
    )
    assert abs(fast["ci95_low"] - np.quantile(slow, .025)) < 1e-12
    assert abs(fast["ci95_high"] - np.quantile(slow, .975)) < 1e-12
    assert abs(
        fast["p_uplift_le_zero"] - np.mean(slow <= 0)
    ) < 1e-12
