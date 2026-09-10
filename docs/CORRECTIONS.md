# Engineering Corrections

## CORR-ENG-001 — Remote URI was treated as local Path

Found before deployment.

Problem:
The first INGEST-003 draft accepted `CANONICAL_ROOT` / `MANIFEST_ROOT` but passed
them through `pathlib.Path`. A URI such as `s3://bucket/prefix` would therefore
not be a real remote object-store target.

Fix:
Added `storage.py` using PyArrow FileSystem with explicit local/S3 support,
including S3-compatible `AWS_ENDPOINT_URL`. Raw ZIP, CHECKSUM, canonical Parquet,
and final manifest now publish through the same remote store abstraction.

Scientific impact:
None. No real market dataset or EXP-001 result had been generated.


## CORR-ENG-002 — Canonical object hash absent from remote manifest

Found before deployment.

Problem:
The first remote publisher recorded the Binance ZIP hash but not the exact
canonical Parquet bytes used by research. That was insufficient to freeze a
fully reproducible DatasetSnapshot.

Fix:
Canonical Parquet is serialized deterministically per ingestion run, SHA-256 is
recorded in the ingest manifest, and DatasetSnapshot binds every monthly
manifest/source/canonical hash.

Scientific impact:
None. No real Discovery result existed.


## CORR-ENG-003 — Internal continuity did not prove full-month coverage

Found before deployment.

Problem:
A month could be internally continuous yet omit the first or final 5m bar.
The previous audit would incorrectly accept that archive.

Fix:
Monthly ingestion now pre-registers exact UTC month start, exact final 5m open,
calendar-aware expected row count (including leap years), 5m grid alignment,
and `close_time = open_time + 5m - 1ms`.

Scientific impact:
None. No real Discovery dataset had been published.


## CORR-ENG-004 — Railway Bucket virtual-host addressing

Found before deployment.

Problem:
Railway's current Buckets use virtual-hosted-style S3 URLs. With a non-AWS
endpoint override, PyArrow does not necessarily force virtual addressing unless
explicitly requested.

Fix:
`S3FileSystem(..., force_virtual_addressing=True)` is enabled when `ENDPOINT` /
`AWS_ENDPOINT_URL` is set. Railway-native bucket variable names are accepted.

Scientific impact:
None.


## CORR-ENG-005 — Interrupted remote publish was not resumable

Found before deployment.

Problem:
A crash after RAW/Parquet writes but before the final manifest would leave
orphan objects. A naive rerun would stop on "already exists" forever.

Fix:
Normal reruns now hash-verify completed manifests and safely skip them.
Orphan objects are reused only when their exact expected hashes match; any
mismatch stops the pipeline without overwrite.

Scientific impact:
None.


## CORR-ENG-006 — Futures CSV header assumption removed

Found before deployment.

Problem:
External documentation is inconsistent about whether Binance futures archive
CSVs include a header row. A parser hard-coded to headerless input could either
fail or silently mishandle a source-format change.

Fix:
The parser inspects the first token, supports both headerless and headered
12-column Kline CSVs, enforces exactly 12 fields, and rejects unexpected
timestamp units/ranges rather than silently converting them.

Scientific impact:
None.


## OPT-ENG-001 — Free-tier EXP-001 memory / bootstrap optimization

Implemented before real Discovery.

Change:
Rolling quantile thresholds are computed one at a time and discarded rather
than retained as 22 wide columns. Forward metrics are processed one horizon at
a time. The moving-block bootstrap uses cumulative block aggregates instead of
allocating a full resampled row-index vector on every repetition.

The bootstrap equivalence test compares the optimized implementation with the
original explicit block-concatenation algorithm under the same RNG seed.

Scientific definition:
Unchanged.

## CORR-ENG-007 — Restore final four hours as label observations

The received ZIP matched all 36 entries in its original manifest, but still
truncated the input at 2023-12-31 19:55 before computing forward labels. The
earlier conversation claimed this was fixed; that fix was absent from this ZIP.
Keep observations through 23:55 and mask scored signals only after calculating
labels. Deterministic next-open/4h boundary and complete reference-engine
equivalence tests now pass. Research constants and label formulas are unchanged.

## CORR-ENG-008 — Verify the frozen dataset at the actual research read

The original runner checked status and phase but never rehashed the bound
canonical files or manifest. It also accepted arbitrary paths and date coverage.
The research reader now validates exact 48-month Discovery scope before object
access, verifies snapshot, monthly manifests, official source checksums, RAW
archives and Parquet bytes, and audits the exact bytes passed to research.
Production CLI requires the frozen 500 bootstrap repetitions. Validation/OOS
paths are rejected before any referenced object is read.

## CORR-ENG-009 — Reject null/nonfinite fields and nonpositive prices

Polars comparison filters alone can miss null source fields. The acceptance
gate now explicitly rejects null, NaN and infinite required values, and zero or
negative OHLC. Extreme but finite valid market observations remain accepted.

## CORR-ENG-010 — Complete the single-runner execution and provenance

The received workflows still required S3 secrets. Added one standard Ubuntu
workflow using runner-local storage, followed by a 3-day artifact containing the
RAW, canonical, manifest and research evidence. Upload size is capped at 200 MiB.
No paid service is created. Runtime dependencies, recovered protocol semantics
and release source hashes are recorded. This artifact is temporary storage;
permanent object storage has not been provisioned. Original remote workflows and
original REPO_MANIFEST remain as provenance, not as the zero-cost entry point.

All corrections above preceded any real Discovery computation in this task.
Local validation: 15 pytest tests passed, plus both original stdlib self-tests.
The first full test attempt had an inaccessible default pytest temp directory;
rerunning with a task-local temporary directory passed all 15 tests.
