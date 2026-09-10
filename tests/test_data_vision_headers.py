import io
import zipfile

from quant_platform.ingest.binance_data_vision import parse_usdm_kline_zip


ROW = (
    "1704067200000,42000,42100,41900,42050,10,"
    "1704067499999,420500,100,5,210250,0"
)
HEADER = (
    "open_time,open,high,low,close,volume,close_time,quote_volume,"
    "count,taker_buy_volume,taker_buy_quote_volume,ignore"
)


def make_zip(text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("x.csv", text)
    return b.getvalue()


def test_headerless():
    df = parse_usdm_kline_zip(make_zip(ROW + "\n"))
    assert df.height == 1
    assert df["open_time"][0] == 1704067200000


def test_header_is_tolerated():
    df = parse_usdm_kline_zip(make_zip(HEADER + "\n" + ROW + "\n"))
    assert df.height == 1
    assert df["trade_count"][0] == 100
