from __future__ import annotations

import math
import numpy as np
import pandas as pd
from pandas.api.indexers import FixedForwardWindowIndexer

HORIZONS={"5m":1,"15m":3,"30m":6,"1h":12,"4h":48}
BUCKETS=[
    "00-10","10-20","20-30","30-40","40-50","50-60",
    "60-70","70-80","80-90","90-95","95-99","99+",
]
BUCKET_SCORE={b:i for i,b in enumerate(BUCKETS)}
QS=[.10,.20,.30,.40,.50,.60,.70,.80,.90,.95,.99]
W=8640
MINP=2016
SAME_SLOT_DAYS=30
SAME_SLOT_MIN=14
FUNDING_CENTERS=[0,480,960]
FUNDING_HALF_WINDOW=30


def _funding_window(ts:pd.Series)->pd.Series:
    mins=ts.dt.hour*60+ts.dt.minute
    out=np.zeros(len(ts),dtype=bool)
    for c in FUNDING_CENTERS:
        d=(mins-c).abs()
        d=np.minimum(d,1440-d)
        out |= d<=FUNDING_HALF_WINDOW
    return pd.Series(out,index=ts.index)


def _assign_rolling_buckets(df,value_col,prefix):
    past=df[value_col].shift(1)
    roll=past.rolling(W,min_periods=MINP)
    qcols={}
    for q in QS:
        c=f"{prefix}_q_{int(q*100):02d}"
        df[c]=roll.quantile(q)
        qcols[q]=c
    x=df[value_col]
    conds=[x<df[qcols[q]] for q in QS]
    df[f"{prefix}_bucket"]=np.select(conds,BUCKETS[:-1],default="99+")
    df.loc[df[qcols[.50]].isna(),f"{prefix}_bucket"]=None
    df[f"{prefix}_bucket_score"]=df[f"{prefix}_bucket"].map(BUCKET_SCORE)
    return df


def _same_slot_median(df,value_col,out_col):
    df[out_col]=(
        df.groupby("slot_of_day",sort=False)[value_col]
        .transform(lambda s:s.shift(1).rolling(
            SAME_SLOT_DAYS,min_periods=SAME_SLOT_MIN
        ).median())
    )
    return df


def build_features(df:pd.DataFrame)->pd.DataFrame:
    x=df.sort_values("source_open_time_ms").reset_index(drop=True).copy()
    x["open_time"]=pd.to_datetime(x["source_open_time_ms"],unit="ms",utc=True)
    x["slot_of_day"]=x["open_time"].dt.hour*12+x["open_time"].dt.minute//5
    x["is_weekend"]=x["open_time"].dt.weekday>=5
    x["is_funding_window"]=_funding_window(x["open_time"])

    # Recompute from canonical source columns as a consistency defense.
    x["taker_sell_quote_volume"]=x["quote_volume"]-x["taker_buy_quote_volume"]
    x["delta_quote"]=2*x["taker_buy_quote_volume"]-x["quote_volume"]
    x["delta_ratio"]=np.where(
        x["quote_volume"]>0,x["delta_quote"]/x["quote_volume"],np.nan
    )
    rng=x["high"]-x["low"]
    x["clv"]=np.where(rng>0,(x["close"]-x["low"])/rng,.5)
    x["log_quote_volume"]=np.log1p(x["quote_volume"])

    past=x["log_quote_volume"].shift(1)
    roll=past.rolling(W,min_periods=MINP)
    x["raw_volume_mean_30d"]=roll.mean()
    x["raw_volume_std_30d"]=roll.std()
    x["raw_volume_z"]=(x["log_quote_volume"]-x["raw_volume_mean_30d"])/(
        x["raw_volume_std_30d"].replace(0,np.nan)
    )
    x=_assign_rolling_buckets(x,"log_quote_volume","raw_volume")

    x=_same_slot_median(
        x,"log_quote_volume","expected_log_volume_same_slot"
    )
    x["volume_surprise"]=(
        x["log_quote_volume"]-x["expected_log_volume_same_slot"]
    )
    x=_assign_rolling_buckets(x,"volume_surprise","volume_surprise")
    return x


def add_forward_targets(df:pd.DataFrame)->pd.DataFrame:
    x=df.copy()
    entry=x["open"].shift(-1)
    logret=np.log(x["close"]/x["close"].shift(1))

    for label,h in HORIZONS.items():
        x[f"fwd_ret_{label}"]=x["close"].shift(-h)/entry-1
        x[f"fwd_abs_ret_{label}"]=x[f"fwd_ret_{label}"].abs()

        fw=FixedForwardWindowIndexer(window_size=h)
        future_high=x["high"].shift(-1).rolling(fw,min_periods=h).max()
        future_low=x["low"].shift(-1).rolling(fw,min_periods=h).min()
        x[f"mfe_{label}"]=future_high/entry-1
        x[f"mae_{label}"]=future_low/entry-1

        # Market forward RV: close-to-close realized variation over future bars.
        future_sq=logret.shift(-1).pow(2).rolling(fw,min_periods=h).sum()
        x[f"fwd_rv_{label}"]=np.sqrt(future_sq)

        x=_same_slot_median(
            x,f"fwd_rv_{label}",f"expected_fwd_rv_same_slot_{label}"
        )
        x=_same_slot_median(
            x,f"fwd_abs_ret_{label}",f"expected_abs_ret_same_slot_{label}"
        )
        er=x[f"expected_fwd_rv_same_slot_{label}"].replace(0,np.nan)
        ea=x[f"expected_abs_ret_same_slot_{label}"].replace(0,np.nan)
        x[f"excess_rv_ratio_{label}"]=x[f"fwd_rv_{label}"]/er-1
        x[f"excess_abs_ret_ratio_{label}"]=x[f"fwd_abs_ret_{label}"]/ea-1
    return x


def bucket_summary(df,bucket_col,adjusted):
    rows=[]
    for b in BUCKETS:
        g=df[df[bucket_col]==b]
        row={"bucket":b,"n":int(len(g))}
        for label in HORIZONS:
            row[f"abs_ret_{label}"]=g[f"fwd_abs_ret_{label}"].mean()
            row[f"rv_{label}"]=g[f"fwd_rv_{label}"].mean()
            row[f"mfe_{label}"]=g[f"mfe_{label}"].mean()
            row[f"mae_abs_{label}"]=(-g[f"mae_{label}"]).mean()
            row[f"signed_ret_{label}"]=g[f"fwd_ret_{label}"].mean()
            if adjusted:
                row[f"excess_rv_ratio_{label}"]=g[
                    f"excess_rv_ratio_{label}"
                ].mean()
                row[f"excess_abs_ret_ratio_{label}"]=g[
                    f"excess_abs_ret_ratio_{label}"
                ].mean()
        rows.append(row)
    return pd.DataFrame(rows)


def monotonicity(summary,col):
    s=summary[["bucket",col]].dropna().copy()
    if len(s)<3:
        return np.nan
    a=s["bucket"].map(BUCKET_SCORE).astype(float)
    b=s[col].rank(method="average").astype(float)
    return float(a.corr(b))


def block_bootstrap_uplift(
    df, metric, score_col, reps=500, block=288, seed=20260910
):
    z=df[[metric,score_col]].dropna().reset_index(drop=True)
    n=len(z)
    if n<block*4:
        return {"metric":metric,"error":"insufficient_rows"}
    vals=z[metric].to_numpy(float)
    score=z[score_col].to_numpy(float)
    rng=np.random.default_rng(seed)

    def uplift(v,s):
        hi=v[s>=BUCKET_SCORE["90-95"]]
        base=v[s<BUCKET_SCORE["80-90"]]
        if len(hi)==0 or len(base)==0:
            return np.nan
        return float(hi.mean()-base.mean())

    obs=uplift(vals,score)
    diffs=[]
    nblocks=math.ceil(n/block)
    max_start=n-block
    for _ in range(reps):
        starts=rng.integers(0,max_start+1,size=nblocks)
        idx=np.concatenate([np.arange(s,s+block) for s in starts])[:n]
        d=uplift(vals[idx],score[idx])
        if np.isfinite(d):
            diffs.append(d)
    arr=np.asarray(diffs)
    return {
        "metric":metric,
        "observed_uplift":obs,
        "bootstrap_reps":int(len(arr)),
        "ci95_low":float(np.quantile(arr,.025)),
        "ci95_high":float(np.quantile(arr,.975)),
        "p_uplift_le_zero":float(np.mean(arr<=0)),
    }


def run_discovery(df,reps=500):
    # Snapshot itself is Discovery-only and already ends at 2023-12-31.
    # Purge final 4h so no forward target leaves the frozen interval.
    x=df.copy()
    cutoff=pd.Timestamp("2023-12-31 19:55:00",tz="UTC")
    x=x[x["open_time"]<=cutoff].copy()

    raw=bucket_summary(x,"raw_volume_bucket",False)
    adj=bucket_summary(x,"volume_surprise_bucket",True)

    regimes={}
    masks={
        "weekday":~x["is_weekend"],
        "weekend":x["is_weekend"],
        "funding_window":x["is_funding_window"],
        "non_funding":~x["is_funding_window"],
    }
    for name,mask in masks.items():
        regimes[name]=bucket_summary(
            x[mask],"volume_surprise_bucket",True
        )

    raw_mono={}; adj_mono={}; raw_boot={}; adj_boot={}
    for label in HORIZONS:
        for sm,bar in [
            (f"abs_ret_{label}",f"fwd_abs_ret_{label}"),
            (f"rv_{label}",f"fwd_rv_{label}"),
        ]:
            raw_mono[sm]=monotonicity(raw,sm)
            raw_boot[sm]=block_bootstrap_uplift(
                x,bar,"raw_volume_bucket_score",reps=reps
            )
        for m in [
            f"excess_abs_ret_ratio_{label}",
            f"excess_rv_ratio_{label}",
        ]:
            adj_mono[m]=monotonicity(adj,m)
            adj_boot[m]=block_bootstrap_uplift(
                x,m,"volume_surprise_bucket_score",reps=reps
            )

    return {
        "rows_scored":int(len(x)),
        "raw":raw,
        "adjusted":adj,
        "regimes":regimes,
        "stats":{
            "EXP-001A_raw_monotonicity":raw_mono,
            "EXP-001B_adjusted_monotonicity":adj_mono,
            "EXP-001A_raw_bootstrap":raw_boot,
            "EXP-001B_adjusted_bootstrap":adj_boot,
        },
    }
