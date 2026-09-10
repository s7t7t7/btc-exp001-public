from dataclasses import dataclass, asdict
import polars as pl

BAR_MS=300_000

@dataclass(frozen=True)
class KlineAudit:
    rows:int
    duplicate_timestamps:int
    non_5m_steps:int
    missing_grid_bars:int
    open_grid_mismatch:int
    close_time_mismatch:int
    boundary_mismatch:int
    expected_rows_mismatch:int
    bad_ohlc:int
    negative_volume:int
    negative_trade_count:int
    bad_taker_buy_base:int
    bad_taker_buy_quote:int
    availability_mismatch:int
    min_open_time_ms:int|None
    max_open_time_ms:int|None

    @property
    def passed(self):
        return all(v==0 for v in (
            self.duplicate_timestamps,self.non_5m_steps,self.missing_grid_bars,
            self.open_grid_mismatch,self.close_time_mismatch,
            self.boundary_mismatch,self.expected_rows_mismatch,
            self.bad_ohlc,self.negative_volume,self.negative_trade_count,
            self.bad_taker_buy_base,self.bad_taker_buy_quote,
            self.availability_mismatch,
        ))

    def to_dict(self):
        d=asdict(self)
        d["passed"]=self.passed
        return d

def audit_5m(
    df,
    *,
    expected_start_ms:int|None=None,
    expected_end_ms:int|None=None,
    expected_rows:int|None=None,
):
    # Null/NaN values can disappear from comparison filters; reject explicitly.
    required = ["source_open_time_ms", "source_close_time_ms", "available_at_ms",
                "open", "high", "low", "close", "base_volume", "quote_volume",
                "trade_count", "taker_buy_base_volume", "taker_buy_quote_volume"]
    if df.select(pl.any_horizontal([
        pl.col(c).is_null() | ~pl.col(c).is_finite() for c in required
    ]).any()).item():
        raise ValueError("Data Acceptance Gate: null or non-finite source field")
    if df.filter(pl.any_horizontal([pl.col(c) <= 0 for c in ["open", "high", "low", "close"]])).height:
        raise ValueError("Data Acceptance Gate: non-positive price")
    x=df.sort("source_open_time_ms")
    if x.height==0:
        return KlineAudit(
            0,0,0,0,0,0,
            1 if expected_start_ms is not None else 0,
            1 if expected_rows not in (None,0) else 0,
            0,0,0,0,0,0,None,None
        )

    dup=(x.group_by("source_open_time_ms").len()
          .filter(pl.col("len")>1)
          .select((pl.col("len")-1).sum()).item() or 0)

    ds=x.select(pl.col("source_open_time_ms").diff().alias("d")).drop_nulls()
    non5=ds.filter(pl.col("d")!=BAR_MS).height

    mn=int(x["source_open_time_ms"].min())
    mx=int(x["source_open_time_ms"].max())
    internal_expected=((mx-mn)//BAR_MS)+1
    missing=max(0,internal_expected-int(x["source_open_time_ms"].n_unique()))

    open_grid=x.filter(pl.col("source_open_time_ms")%BAR_MS!=0).height
    close_bad=x.filter(
        pl.col("source_close_time_ms")!=pl.col("source_open_time_ms")+BAR_MS-1
    ).height

    boundary_bad=0
    if expected_start_ms is not None and mn!=expected_start_ms:
        boundary_bad+=1
    if expected_end_ms is not None and mx!=expected_end_ms:
        boundary_bad+=1

    rows_bad=0
    if expected_rows is not None and x.height!=expected_rows:
        rows_bad=abs(int(expected_rows)-x.height)

    bad_ohlc=x.filter(
        (pl.col("high")<pl.col("open"))|(pl.col("high")<pl.col("close"))|
        (pl.col("high")<pl.col("low"))|(pl.col("low")>pl.col("open"))|
        (pl.col("low")>pl.col("close"))|(pl.col("low")>pl.col("high"))
    ).height

    negv=x.filter(
        (pl.col("base_volume")<0)|(pl.col("quote_volume")<0)
    ).height
    negc=x.filter(pl.col("trade_count")<0).height
    badb=x.filter(
        (pl.col("taker_buy_base_volume")<0)|
        (pl.col("taker_buy_base_volume")>pl.col("base_volume"))
    ).height
    badq=x.filter(
        (pl.col("taker_buy_quote_volume")<0)|
        (pl.col("taker_buy_quote_volume")>pl.col("quote_volume"))
    ).height
    bada=x.filter(
        pl.col("available_at_ms")!=pl.col("source_close_time_ms")+1
    ).height

    return KlineAudit(
        x.height,int(dup),int(non5),int(missing),int(open_grid),int(close_bad),
        int(boundary_bad),int(rows_bad),int(bad_ohlc),int(negv),int(negc),
        int(badb),int(badq),int(bada),mn,mx
    )
