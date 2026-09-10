# BTC EXP-001 Discovery

Research on whether unexpected BTCUSDT perpetual trading volume predicts future
volatility. Uses Binance Data Vision USD-M 5m archives for **2020–2023 only**.
This is a market-effect study, not a trading system. Validation and OOS remain locked.

## Run

The **BTC EXP-001 zero-cost Discovery** GitHub Actions workflow uses one standard
Ubuntu runner and its local disk. It requires no S3, Railway or exchange credentials.
It runs on a push to `main` or manual dispatch, verifies the release, runs offline
tests, downloads 48 official archives, audits every month, freezes the snapshot,
rechecks all hashes and runs the existing EXP-001A/001B with 500 bootstrap draws.

The resulting artifact includes raw archives, canonical data, manifests and research
outputs, is capped at 200 MiB before upload, and expires after **3 days**. Retrieve
the evidence before expiry. This temporary artifact is the persistence mechanism
for this run; no permanent cloud archive is provisioned. Do not repeatedly dispatch
runs or enable paid runners. The older workflows are retained for provenance and
require an independently configured object store; do not use them for this run.

## Provenance and corrections

`REPO_MANIFEST.json` is the unchanged original package manifest; its 36 entries were
verified before editing. `RELEASE_MANIFEST.json` records this engineering revision
and the original ZIP SHA256. Bytecode from the uploaded archive is not published.
`FROZEN_PROTOCOL_v1_1.json` records the exact recovered source constants and semantics.
Corrections in `docs/CORRECTIONS.md` distinguish the received files from prior chat
claims. No real Discovery result was inspected before these corrections.

Local verification:

```sh
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
python scripts/verify_release.py
python -m pytest -q
```

Read `research/results/EXP-001/v1.1/discovery/*/evidence.json` and `stats.json` in
the artifact. A computed result still requires review. The bootstrap tail fraction
is not a calibrated p-value; these correlated horizon comparisons do not establish
causation, out-of-sample robustness or profitable execution.
