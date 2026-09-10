# Railway Deployment Gate

## Components

Initial research deployment uses exactly:

1. one Railway service: `quant-research`
2. one Railway Storage Bucket: `quant-data`

No database and no Binance trading credentials.

## Railway Bucket variables

The worker accepts Railway's native bucket variables directly:

- `BUCKET`
- `ACCESS_KEY_ID`
- `SECRET_ACCESS_KEY`
- `REGION`
- `ENDPOINT`

`storage.py` forces virtual-hosted addressing for non-AWS endpoint overrides,
matching current Railway Bucket URL behavior.

## Safe first deployment

The default container command runs:

`QUANT_JOB_MODE=smoke`

This only:
- constructs the remote S3 client,
- writes a runtime/environment fingerprint,
- exits successfully.

It does NOT download market history.

## Full research permission gate

Only after explicit cost approval, set:

`QUANT_JOB_MODE=full-discovery`

The same worker then performs:

1. 48 monthly BTCUSDT 5m Data Vision fetches (2020-2023)
2. official checksum verification
3. semantic full-month data audit
4. resumable immutable publish
5. Discovery DatasetSnapshot freeze
6. EXP-001 v1.1 Discovery
7. evidence write to the same bucket

## Recovery semantics

A completed monthly manifest is re-verified and skipped.

If a previous run died after writing one or more objects but before the final
manifest, the next run compares exact hashes:
- equal -> safely reuses the object and commits the manifest
- different -> stops with conflict; never overwrites silently

This is `at-least-once fetch + idempotent publish`.
