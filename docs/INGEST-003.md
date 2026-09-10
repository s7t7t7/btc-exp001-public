# INGEST-003 v1.0

Frozen scope: Binance USD-M BTCUSDT perpetual, 5m, Discovery 2020-2023.

Pipeline:

Data Vision monthly ZIP
-> sibling CHECKSUM
-> SHA-256 verify
-> headerless Kline CSV parse
-> canonical availability timestamp (`close_time + 1ms`)
-> data gate
-> immutable Parquet publish
-> manifest
-> frozen DatasetSnapshot

Normal publish never overwrites an existing canonical month.
Any correction must use an explicit repair/supersede workflow.

GitHub Actions workflow `backfill-discovery.yml` supports month-index ranges so
large backfills can be split across remote runs without changing the protocol.
