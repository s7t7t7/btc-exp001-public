from __future__ import annotations
import csv, hashlib, io, re, zipfile

BAR_MS=300_000

def build_archive_path(symbol="BTCUSDT",interval="5m",year=2024,month=1):
    name=f"{symbol}-{interval}-{year:04d}-{month:02d}.zip"
    path=f"data/futures/um/monthly/klines/{symbol}/{interval}/{name}"
    return name,path

def parse_checksum(text,expected):
    m=re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)",text.strip())
    assert m and m.group(2)==expected
    return m.group(1).lower()

def make_zip(rows):
    b=io.BytesIO()
    with zipfile.ZipFile(b,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("BTCUSDT-5m-2024-01.csv","\n".join(rows)+"\n")
    return b.getvalue()

def parse_rows(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        names=[n for n in z.namelist() if n.endswith(".csv")]
        assert len(names)==1
        text=z.read(names[0]).decode()
    return list(csv.reader(io.StringIO(text)))

def audit(rows):
    ts=[int(r[0]) for r in rows]
    assert len(ts)==len(set(ts)), "duplicate timestamp"
    for a,b in zip(ts,ts[1:]):
        assert b-a==BAR_MS, "gap/non-5m step"
    for r in rows:
        o,h,l,c=map(float,r[1:5])
        base=float(r[5]); quote=float(r[7]); trades=int(r[8])
        tbb=float(r[9]); tbq=float(r[10])
        assert h>=max(o,l,c) and l<=min(o,h,c)
        assert base>=0 and quote>=0 and trades>=0
        assert 0<=tbb<=base and 0<=tbq<=quote
        close_ms=int(r[6])
        available_at_ms=close_ms+1
        assert available_at_ms-int(r[0])==BAR_MS
    return True


def calendar_month_expected_rows(year,month):
    from datetime import datetime,timezone
    a=datetime(year,month,1,tzinfo=timezone.utc)
    if month==12:
        b=datetime(year+1,1,1,tzinfo=timezone.utc)
    else:
        b=datetime(year,month+1,1,tzinfo=timezone.utc)
    return int((b-a).total_seconds()//300)

def main():
    name,path=build_archive_path()
    assert calendar_month_expected_rows(2024,1)==31*288
    assert calendar_month_expected_rows(2024,2)==29*288
    assert calendar_month_expected_rows(2023,2)==28*288
    assert path=="data/futures/um/monthly/klines/BTCUSDT/5m/BTCUSDT-5m-2024-01.zip"
    rows=[
      "1704067200000,42000,42100,41900,42050,10,1704067499999,420500,100,5,210250,0",
      "1704067500000,42050,42200,42000,42100,12,1704067799999,505200,120,7,294700,0",
      "1704067800000,42100,42300,42050,42250,11,1704068099999,464750,110,5.5,232375,0",
    ]
    payload=make_zip(rows)
    sha=hashlib.sha256(payload).hexdigest()
    assert parse_checksum(f"{sha}  {name}",name)==sha
    parsed=parse_rows(payload)
    assert audit(parsed)

    # Gap detection must fail.
    try:
        audit([parsed[0],parsed[2]])
        raise AssertionError("gap was not rejected")
    except AssertionError as e:
        assert "gap" in str(e)

    # Taker-buy > total must fail.
    bad=parsed.copy()
    b=bad[0].copy(); b[10]="500000"; bad[0]=b
    try:
        audit(bad)
        raise AssertionError("bad taker value was not rejected")
    except AssertionError:
        pass

    print("INGEST-003 stdlib offline self-test: PASS")

if __name__=="__main__":
    main()
