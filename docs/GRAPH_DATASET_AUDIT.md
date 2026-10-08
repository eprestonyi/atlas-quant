# Independent dataset/3 archive audit

`scripts/audit-graph-dataset.py` verifies the `financial_snapshot_graph_50_v1`
source closure with Python's standard library. It imports only the existing
independent `dataset_audit.py` helpers, never engine/graph codecs. The legacy
script and its canonical byte semantics are unchanged; legacy readers reject
version 3.

The audit checks the closed manifest, exact directory or strict USTAR layout,
part lengths/hashes, physical component roots and optional caller-pinned dataset
root. It independently expands sorted dependency/calendar dictionaries, original
ordered events and assignments, exact daily panel cells, coverage and original
prepared roots/payloads. Expanded canonical documents are hashed incrementally,
one prepared graph at a time. Event expansions have the 32 MiB guard; each
prepared payload has the 64 MiB guard. The joined row array and complete joined
rows-plus-provenance document retain the 24 MiB guard.

The column table preserves integer/float distinctions, signed floating zero,
nulls, column order, missing-key distinctions and first-occurrence dictionaries.
The original market source retains its exact bytes; joined market numbers use
the existing int-to-float preparation conversion. Token comparison, rather than
Python numerical equality, checks this conversion and financial graph joins.
Package, snapshot, registry, lineage and coverage references are checked against
retained raw source records. Financial formulas and StatementRecord hashes are
not recalculated by this auditor.

For a pinned read-only audit:

```sh
python3 -S scripts/audit-graph-dataset.py /path/to/dataset-or.tar \
  --expected-root CALLER_AUTHORIZED_DATASET_SHA256 \
  --registry-pins /path/to/independently-authorized-registry-pins.json \
  --source-pins /path/to/independently-retained-source-pins.json \
  --output /path/to/new-audit-result.json
```

Registry pins retain the legacy UUID-to-local-file format. Source pins map
`sourceManifest`, `sourceSnapshot` and every `financialInputN` to independently
retained local files. Relative paths resolve against the pin-map file. The set
must match exactly. Pin files must be authorized independently; extracting
files from the archive and supplying them back does not authenticate it.
An existing result file is never overwritten.

Successful closure reports continue to state `sourceAuthorityVerified: false`,
`financialFormulasRecomputed: false`, `pdfAuthenticityVerified: false`,
`providerAuthenticityVerified: false` and `modelAdmissionRegistered: false`.
Exact external registry/source matches and dataset-root pinning have their own
fields. No audit result substitutes for fresh financial formula recomposition,
provider/PDF authentication, model fitting or a hosted/release gate.

## Retained source-only evidence, 2026-10-08

A Python `-S` run audited the existing artificial 50-security dataset/3 from
`financial-graph-source-50-20261008-02`, without provider/model calls:

- Dataset root: `cca665750bd7ba9d5eacc7ac7b0e54418397c35bf7f22fbb1981349d3cad3172`.
- Ten components, 60 parts, 28,924,215 physical closure bytes.
- 13,100 rows, 16 states, two financial source packages; external original
  manifest, snapshot, package and registry bytes matched.
- Complete logical joined document: 24,923,037 bytes,
  SHA-256 `8077543843d06f7f575f606da27ec4fa378155be5e716435aed9cf4ea5ff072b`.
- 26,200 original market numeric tokens converted as prescribed.
- Original prepared payloads: 33,888,059 and 33,877,368 bytes, processed
  sequentially. Their total 67,765,427 bytes is not retained as a combined
  canonical document.
- `/usr/bin/time -l`: 6.36 seconds and 260,423,680 bytes maximum RSS. This is a
  measurement, not an actively enforced resource supervisor claim.

Private evidence remains under
`work/atlas-quant-graph-audit/private/graph-audit-50-20261008-01/`.
The source-only failure and earlier incorrect component-only normalization
oracle remain retained in their original financial worktree. There was no
successful 50-security dataset/2 root to compare. The small actual dataset/2
fixture provides exact old/new joined bytes and financial-root comparison.

The 29 focused tests cover that real small-fixture oracle, strict USTAR and
layout failures, external pin mismatches, altered/unused/dangling dictionaries,
assignment gaps/overlaps, future availability, incorrect roots, complete
coverage, exact numeric tokens and a fully rehashed `1.0`-to-`1` attack. A
subprocess running Python `-S` proves that the CLI needs no engine or third-party
runtime. Source archive checking is separate from financial result bundle/2
checking; no result bundle or completed 50-security F is claimed here.
