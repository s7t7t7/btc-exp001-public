# RC-001 Remote Execution Chain

The production research path is now:

1. `backfill-discovery.yml`
   - fetches Binance Data Vision monthly BTCUSDT 5m archives
   - verifies official SHA-256
   - applies semantic data gate
   - writes immutable RAW + canonical Parquet + monthly manifests

2. `freeze-discovery-snapshot.yml`
   - requires all 48 monthly manifests (2020-01 through 2023-12)
   - checks month-to-month 5m continuity
   - binds source hash + canonical Parquet hash
   - writes immutable `DS-BTCUSDT-5M-DISCOVERY-v1`

3. `exp001-discovery.yml`
   - accepts only the frozen Discovery snapshot
   - computes EXP-001A raw-volume baseline
   - computes EXP-001B seasonality-adjusted primary evidence
   - writes evidence with snapshot hash
   - does not access Validation or OOS data

No Binance trading credentials are required or permitted in this chain.
