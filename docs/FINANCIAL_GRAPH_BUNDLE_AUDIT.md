# Independent financial bundle/2 and dataset/3 paired audit

`scripts/audit-financial-graph-bundle.py` is a new standard-library verifier for
local `atlas.quant.financial_bundle/2` result directories or exact USTAR archives.
It uses the unchanged independent legacy forecast identity, SQLite indexing and
archive helpers, with separate new manifest/collection/snapshot validation. No
production engine, graph codec, model or provider is imported. Legacy financial
bundle/1 and dataset/1–2 auditors remain unchanged and reject the new formats.

The result transport retains the original forecast/report/coverage canonical
codec and validates every declared collection, chunk hash, order/count, document
recipe and full logical document hash. It verifies forecast-origin plans,
forecast/baseline counts and references, causal fit cutoffs, numerical forecast
identities and disabled execution. Cross-record indices are private temporary
SQLite files; no archived result is rewritten.

The snapshot has the exact schema/3 nine-key contract and transports complete
columns through `snapshotColumns` at `/numericInput/columns`. Financial numeric
tokens retain integer/float distinctions, signed zero and null. Both the actual
physical snapshot and its full logical expansion, including metadata and
provenance, remain within 24 MiB. No remote replacement data is consulted.

With a source sidecar, the independent dataset/3 auditor checks the full source
closure and original logical roots. The pair must have exactly matching dataset
identity, scope, financial commitment, provenance and every ordered numerical
column. Rehashing an altered snapshot cannot satisfy the unchanged source table.
Without the source sidecar the result is `INCOMPLETE_SOURCE`, CLI exit 2.

```sh
python3 -S scripts/audit-financial-graph-bundle.py /path/to/result.tar \
  --source-dataset /path/to/dataset.tar \
  --expected-bundle-id CALLER_AUTHORIZED_BUNDLE_SHA256 \
  --expected-dataset-root CALLER_AUTHORIZED_DATASET_SHA256 \
  --registry-pins /path/to/independently-authorized-registry-pins.json \
  --source-pins /path/to/independently-retained-source-pins.json \
  --output /path/to/new-paired-audit.json
```

The source pin formats are documented in `GRAPH_DATASET_AUDIT.md`. Output files
must be new. External root pinning and exact registry/source byte matches are
reported separately. An internally consistent pair cannot authorize its own
registry or originals.

`PASS` means transport and paired source closure passed. The report explicitly
keeps financial formula recomposition, provider/PDF authentication, research
fingerprint recomputation, model fitting/admission, callable-F reevaluation and
research-statistics recomputation false. All supplied model/statistics
collections are retained and hashed; deeper mathematical validation is a
separate gate. No hosted or production readiness is implied.

## Retained small actual-F pair, 2026-10-08

The existing `graph-bundle-small-audit-20261008-01` pair was read once with Python
`-S`; the audit did not run F or any provider:

- Result bundle `393c61bb79bafc1147dc93947410a31f345d33278f8c4d78e4aa5daef9b10d23`.
- Source dataset `b02dc72d1d0776f4c873464cb57b9dbcacbb55756da5ea790d4ff3d449f04f41`.
- 41 forecast rows, 41 baseline rows, 262 exact snapshot rows; registry pins
  came from the retained caller-supplied fixture, not archive extraction.
- Full logical snapshot: 502,824 bytes,
  SHA-256 `0efe248e2ae3a0db120ec3035fe15dd90d27a302fdd0503e2c089822f2b84b90`.
- `/usr/bin/time -l`: 0.25 seconds and 38,961,152 bytes maximum RSS. This is
  measured resource use, not an active supervisor claim.

Evidence is retained in
`work/atlas-quant-graph-audit/private/graph-bundle-audit-small-20261008-01/`.
This fixture contains the earlier six diagnostic joint pairs; the audit does
not relabel it as the later expanded diagnostic design or a 50-security result.

Fifteen focused tests use clearly labeled no-fit transport fixtures. They check
paired exactness, independent root pins, old-reader rejection, self-rehashed
snapshot/commitment/provenance changes, unknown versions/codecs/collections,
archive trailers, unexpected files, altered chunks and Python `-S` CLI behavior.
The real retained actual-F pair above is separate evidence from those fixtures.
