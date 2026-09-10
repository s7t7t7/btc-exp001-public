from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib, io, re, zipfile
import httpx
import polars as pl

BASE_URL = "https://data.binance.vision"
KLINE_COLUMNS = [
    "open_time","open","high","low","close","base_volume","close_time",
    "quote_volume","trade_count","taker_buy_base_volume",
    "taker_buy_quote_volume","ignore",
]

@dataclass(frozen=True)
class ArchiveObject:
    symbol: str
    interval: str
    year: int
    month: int
    @property
    def filename(self):
        return f"{self.symbol}-{self.interval}-{self.year:04d}-{self.month:02d}.zip"
    @property
    def relative_path(self):
        return (
            f"data/futures/um/monthly/klines/{self.symbol}/{self.interval}/"
            f"{self.filename}"
        )
    @property
    def url(self):
        return f"{BASE_URL}/{self.relative_path}"
    @property
    def checksum_url(self):
        return f"{self.url}.CHECKSUM"

@dataclass(frozen=True)
class FetchResult:
    archive: ArchiveObject
    payload: bytes
    checksum_text: str
    official_sha256: str
    actual_sha256: str
    fetched_at: str

def monthly_kline(symbol, interval, year, month):
    return ArchiveObject(symbol.upper(), interval, year, month)

def parse_checksum(text, expected_filename):
    line = next((x.strip() for x in text.splitlines() if x.strip()), "")
    m = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line)
    if not m:
        raise ValueError(f"Malformed checksum file: {line!r}")
    sha, filename = m.group(1).lower(), m.group(2).strip()
    if filename != expected_filename:
        raise ValueError(f"Checksum filename mismatch: {filename!r}")
    return sha

def fetch_archive(archive, timeout=60.0):
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        c = client.get(archive.checksum_url)
        c.raise_for_status()
        official = parse_checksum(c.text, archive.filename)
        r = client.get(archive.url)
        r.raise_for_status()
        payload = r.content
        actual = hashlib.sha256(payload).hexdigest()
        if actual != official:
            raise ValueError(f"SHA-256 mismatch: {official} != {actual}")
        return FetchResult(
            archive, payload, c.text, official, actual,
            datetime.now(timezone.utc).isoformat()
        )

def parse_usdm_kline_zip(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        members = [
            n for n in zf.namelist()
            if not n.endswith("/") and n.lower().endswith(".csv")
        ]
        if len(members) != 1:
            raise ValueError(f"Expected one CSV, got {members}")
        raw = zf.read(members[0])

    first_line = raw.splitlines()[0].strip() if raw.splitlines() else b""
    first_token = first_line.split(b",", 1)[0].strip().lower()
    has_header = not first_token.isdigit()

    df = pl.read_csv(
        io.BytesIO(raw),
        has_header=has_header,
        new_columns=KLINE_COLUMNS,
        infer_schema=False,
        schema_overrides={c: pl.String for c in KLINE_COLUMNS},
    )

    if df.width != len(KLINE_COLUMNS):
        raise ValueError(
            f"Unexpected Kline CSV width: {df.width}, expected {len(KLINE_COLUMNS)}"
        )

    floats = [
        "open","high","low","close","base_volume","quote_volume",
        "taker_buy_base_volume","taker_buy_quote_volume",
    ]
    df = df.with_columns([
        pl.col("open_time").cast(pl.Int64, strict=True),
        pl.col("close_time").cast(pl.Int64, strict=True),
        pl.col("trade_count").cast(pl.Int64, strict=True),
        *[pl.col(c).cast(pl.Float64, strict=True) for c in floats],
    ])

    # INGEST-003 v1.0 is frozen to USD-M historical milliseconds.
    # Never silently reinterpret an unexpected timestamp unit.
    min_ts = int(df["open_time"].min()) if df.height else 0
    max_ts = int(df["open_time"].max()) if df.height else 0
    if df.height and not (
        946_684_800_000 <= min_ts <= 4_102_444_800_000
        and 946_684_800_000 <= max_ts <= 4_102_444_800_000
    ):
        raise ValueError(
            "Unexpected USD-M Kline timestamp unit/range; "
            f"open_time range={min_ts}..{max_ts}"
        )
    return df

def normalize_5m(df):
    return (
        df.select([
            pl.col("open_time").alias("source_open_time_ms"),
            pl.col("close_time").alias("source_close_time_ms"),
            "open","high","low","close","base_volume","quote_volume",
            "trade_count","taker_buy_base_volume","taker_buy_quote_volume",
        ])
        .with_columns([
            (pl.col("source_close_time_ms")+1).alias("available_at_ms"),
            (pl.col("quote_volume")-pl.col("taker_buy_quote_volume"))
                .alias("taker_sell_quote_volume"),
            (2*pl.col("taker_buy_quote_volume")-pl.col("quote_volume"))
                .alias("delta_quote"),
        ])
        .with_columns(
            pl.when(pl.col("quote_volume")>0)
              .then(pl.col("delta_quote")/pl.col("quote_volume"))
              .otherwise(None)
              .alias("delta_ratio")
        )
    )
