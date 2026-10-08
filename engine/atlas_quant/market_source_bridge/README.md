# Frozen market sources, offline tranche A

This package prepares a `FrozenMarketSource` and a view of it without fitting F,
calling a provider, registering a dataset, or resolving any owner grant.
It is not connected to a runner, UI, financial composition or hosted admission.

Supported origins are deliberately limited:

| Origin | Original artifacts retained | Whole-source verification |
| --- | --- | --- |
| `legacy_cache` | Exact input `{rows,provenance}` bytes, including whitespace/order | Exact legacy row fingerprint, closed market columns, all rows/calendar/members, retained adjustment convention |
| `market_dataset` | Exact `market_dataset/1` manifest, plan, scope and every rows/provenance/receipts/raw part | Existing source reader recomposes the complete normalized source from original response bytes |
| `forecast_snapshot` | Not implemented here | Rejected; the existing snapshot reader is unchanged |

The implemented legacy dialect is the historical `TUSHARE_PRO / PROVIDER_DATA`
cache with `private_proxy` or `official_https_rest` transport and its exact
`Tushare SSE official trading calendar; SH/SZ/BJ session alignment assumed`
declaration, plus a named synthetic test-fixture dialect. Unknown source,
transport or calendar dialects fail closed. The quoted calendar string is an
original claim; the retained `tradingDates` array supplies the source calendar.
Neither is independent proof that a venue published those dates.

A legacy cache is **not** transformed into a provider receipt, market publication
or forecast bundle. Its original `source`, `classification`, `synthetic`, date
and request assertions remain visible as declarations. The cache has no retained
raw OHLC or provider-response archive; this bridge cannot authenticate them.
`providerReceiptBytesRetained=true` for a native dataset only says that the response
archive survived; a hash cannot authenticate the vendor that produced it.

All offline artifacts and audit reports keep `ownerGrantVerified=false`,
`filterResolutionVerified=false`, `providerOriginIndependentlyAttested=false` and
`pointInTimeRevisionsVerified=false`, even when all external content pins match.
No result from this package is a model-admission capability.

## API and identities

1. `freeze_legacy_cache(raw, expected_sha256=...)` or
   `freeze_market_dataset(manifest_raw, plan_raw, scope_raw, read_part,
   expected_root=...)` verifies **all** original rows and parts before a view is
   possible. The typed result is not trusted merely because its class matches:
   `derive_view` freshly verifies its originals and exact reconstructed payloads.
2. `derive_view(source, filter_scope_bytes, scope_ref, mode=...)` takes the complete
   frozen `universe_scope/1` used by the caller. There is no selected-members list,
   count truncation, sampling, filling or warmup extension. `mode=exact` requires
   identical full scopes; `explicit_subset` records a filter view of the original
   source. Every filtered member must have observations.
3. `export_view(view, new_directory)` writes a new private directory only. It does
   not overwrite or support archive extraction. A write failure leaves its partial
   new directory for inspection; it is not registration and is never auto-retried.

The original `scopeRoot` is SHA256 of the **exact incoming frozen JSON bytes**, as
in the existing native source reader. Foreign finite JSON encoding (for example
JS `0.000001` versus Python `1e-06`) is not rewritten and relabelled as its original
root. All generated descriptors and `market.json` use version-1 Python finite
JSON: UTF-8, sorted keys, `ensure_ascii=False`, compact separators, no nonfinite
numbers. These new roots are a separate protocol, not JS-canonical root claims.
Numeric int/float and negative-zero identity are retained in generated row JSON.
Original artifact SHA256 pins always cover original bytes, not a re-encoding.

The original adjustment method must be
`OHLC multiplied by adj_factor / first observed adj_factor per symbol`.
Each symbol's original earliest observation ordinal, date and IEEE754 float64
factor are retained. Close must match `raw_close * adj_factor / original_base`
(relative and absolute tolerance 2e-10 for legacy rounded values). Subsetting
never selects a new adjustment base. Original open/high/low are retained; absent
raw open/high/low cannot be reconstructed or independently verified for a cache.

## Bounds and rejection

Whole original closure **plus** descriptors, full filter and derived market must
fit 64 MiB. Original/derived JSON document maximum is 24 MiB, descriptors 256 KiB;
originals allow at most 300,000 rows (legacy cache 110,000), 1,000 SH/SZ members,
580 artifacts; a view permits 50 symbols, 366 inclusive calendar days and 110,000
rows. Limits may only be reduced. Native part sizes are summed before the first
part read; a tiny projection does not bypass a large complete source rejection.
Export reserves the rounded remaining payload plus two filesystem blocks per
new entry in each free-space check, in addition to the 500 MiB floor. Free space
is checked before each directory creation and write, before durability flushes,
and before returning success; this is a current-space guard, not an OS quota
against concurrent writers. Every ancestor and child is opened relative to
retained directory descriptors with `O_DIRECTORY|O_NOFOLLOW`; writes use
`O_EXCL|O_NOFOLLOW` relative to those handles. Directory/file inode identities
are checked during export and before completion. Files, created directories and
their parent entries are fsynced. Short writes and any write/fsync/path/disk
failure propagate, leave the partial output untouched, and never return success.
Platforms lacking no-follow directory-descriptor support fail closed.
These limits describe offline processing, not accepted hosted capacity.

Strict decoders reject duplicate JSON keys, nonfinite tokens, excessive nesting,
unknown closed fields, and numeric booleans. No arbitrary expression, pickle,
provider or model execution exists. Optional registered market fields may be null;
financial/external columns need their own source closure and are rejected here.
Only SH/SZ source identifiers are currently accepted; this tranche does not
introduce Beijing or other market support.

## Independent audit

`python -S tools/audit_market_source_bridge.py DIRECTORY --pins PINS_JSON
--expected-view-root SHA256 --output NEW_JSON` imports no engine or site packages.
For native input it reuses the existing **stdlib** `market_dataset_audit` to
recompute full raw-source normalization. Its external pins schema is:

```
{ "sourceRoot": "sha256", "filterScopeRoot": "sha256",
  "originals": { "cache.json": { "sha256": "sha256", "byteLength": 123 } } }
```

Native `originals` instead includes manifest, plan, scope and all part paths.
All paths and complete bytes are verified; unexpected files/directories,
symlinks, false evidence upgrades and changed projection values/order fail.
Pins should be obtained independently by the caller; merely making pins from
an untrusted archive supplies content equality, not external authority. The CLI
never makes an owner, provider-origin, PIT, financial-formula or model claim.

Tests: `PYTHONPATH=engine .venv/bin/python -m pytest
engine/tests/test_market_source_bridge.py -q`. These are small synthetic source
fixtures and independent audits, with zero fits and zero provider requests.
