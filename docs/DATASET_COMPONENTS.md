# Offline financial dataset components

This is a **local Python implementation**, based on `bfe2855`, of the first typed source-closure slice. It does not enable a hosted `datasetRef`, financial research binding, an HTTP endpoint, a runner capability, a provider fetch, forecast fitting, or financial execution. The v0.7 financial workspace remains independently deployable.

The profile is `financial_compose_50_v1`. It closes one already-frozen market dataset and 1–8 frozen financial packages over exact external calendar/proof registry records, recomputes the existing financial core, and preserves the resulting rows, missing values, coverage, formulas and event lineage. All source evidence here means the **frozen normalized input and registry records**. A document hash without its PDF is not an included original document; historical filing completeness and independent publication-time authenticity are not established by this format.

## Python entry points

The implementation is under `engine/atlas_quant/research_dataset/`:

```python
FinancialSource(package_bytes, prepared_root, calendar_ref, proof_refs=())

publication = compose_dataset_components(
    scope,                  # exact sorted symbols, YYYYMMDD start/end
    canonical_market_bytes,
    financial_sources,
    authorized_registry,    # {registry_uuid: exact canonical bytes}, out of band
    write_part,             # (component_id, ordinal, raw_bytes), private staging
    market_calendar_ref=calendar_ref,
)

reader = DatasetReader(
    publication.manifest_bytes,
    read_part,              # (component_id, ordinal) -> bounded bytes
    expected_root=publication.dataset_root,
)
result = restore_dataset(reader, authorized_registry)
```

`DatasetPublication` contains the canonical manifest bytes and the real `FinancialDatasetResult`. The callback only writes staged parts; a complete returned manifest is the publication boundary. Every byte/count budget is checked before the first callback. A callback failure returns no manifest. The API does not publish to a user-supplied URL or filesystem path.

`restore_dataset` verifies complete transport, matches the archived registry to separately supplied authorized bytes, then calls the same real compose/prepare pipeline. It compares every reconstructed part and the complete manifest. It does not register serialized rows directly. The returned DataFrame is freshly registered by the existing weak-reference admission guard; copying its data, altering values or provenance, or crossing a process boundary requires another reconstruction.

The one shared-core change extracts `financial_runner.trust.resolve_package_registry` from the existing job wrapper. `resolve_input` still performs its original job identity, descriptor, operation and resource checks before/after that helper. No synthetic job or uploaded trust switch is used to invoke the domain layer.

## Exact identity and typed graph

The manifest format is `atlas.quant.research_dataset`, version `1`. The content identity is SHA-256 of the complete canonical manifest bytes; owner-scoped database IDs are not content identity and are not implemented here. A future `datasetRef` retains `{datasetId,datasetRoot,format,version}`.

Each component has a fixed type/version, semantic roots, payload SHA-256/length, exact ordered parts and earlier dependency roots. `componentRoot` hashes its descriptor without itself. The following are distinct and never substituted for one another:

- Actual package byte SHA versus financial `packRoot`.
- Component descriptor root versus component payload SHA.
- Existing `marketRoot`, `preparedRoot`, `calendarRoot`, `financialDatasetRoot` versus outer dataset root.
- Strategy-specific engine `dataSha256` versus the reusable dataset root.

The initial codec deliberately implements only `raw_bytes`: parts preserve the complete canonical source bytes and may split inside a UTF-8 sequence. Decoding occurs only after bounded reconstruction. It does not implement the draft's optional `json_records` encoding.

Registered components are `registryEvidence`, `marketDataset`, `financialInputN`, `financialPreparedN`, `researchRows`, `schema` and `coverage`. Registry is the root; prepared evidence depends on its financial input; the three derived components depend directly on market/prepared components. Keeping schema/coverage as siblings of research rows preserves the draft's maximum depth of three edges. No arbitrary pointer, external URL, code, pickle, cyclic dependency or unregistered file path is interpreted.

Numeric JSON uses the financial core's finite, sorted-key, UTF-8 rules. `1` and `1.0`, and `0.0` and `-0.0`, retain distinct source bytes and roots. The existing market bridge still performs its explicit numerical normalization; the original market bytes remain in the source component. Unknown market columns and inconsistent row dimensions are rejected before the provider validator could silently project them away. Optional registered columns and all financial availability-date columns survive composition and the new snapshot.

## Trust and research admission

The archive cannot authorize its own registry. Every archived registry reference, complete raw record, version, evidence level, scope and payload must equal the separately supplied registry bytes. The existing source-proof checker then matches exact provider/field/row/document coordinates before enabling reviewed core decoding. Declared units remain unverified assumptions. Fixture-only proof bindings cannot cross this boundary, even when the data itself is an explicitly labelled synthetic fixture.

The external Python caller is responsible for authorizing registry records. The library does not authenticate a user, review a PDF, or turn a correctly hashed `official` string into official calendar evidence. A future owner/lease-bound runner must obtain authorized registry bytes from its server; it cannot pass the archive's own contents as authority.

Market session lists must exactly match the selected interval of the authorized complete calendar, whose coverage must contain that interval. The frozen financial package's interval and calendars are checked again by the existing bridge. Financial sources can cover subsets of securities; missing state coverage remains missing. Overlapping security/state sources are rejected instead of choosing an arbitrary winner. The universe and interval for research must equal the frozen scope, with no implicit subset or historical-membership claim. Market provenance may retain its original acquisition order; its symbols must be unique strings with exactly the same membership. Source bytes are never reordered to satisfy this check.

`restore_dataset_for_research(strategy, reader, registry)` additionally admits only schema 2 / statistical quant / fundamental / Ridge / asset price / **explicit execution.enabled=false**. It does not train. Existing financial execution and replay prohibitions remain in place. Dataset reconstruction does not imply enough observations for a forecast: the six-session synthetic case still returns `INSUFFICIENT_DATA` from the real engine preflight.

## Financial snapshot discriminator

The new standalone snapshot helper is:

```python
snapshot = freeze_financial_input(
    strategy, result, dataset_ref,
    manifest_bytes=publication.manifest_bytes,
)
restored = restore_financial_input(
    strategy, canonical_snapshot_bytes, reader, authorized_registry,
)
```

It uses `schemaVersion:2`, `fingerprintVersion:research_input_financial_v1` and `sourceEvidenceClosure:separate_research_dataset_v1`. It preserves every composed column and complete provenance, the real engine numerical fingerprint and financial commitment, and the exact dataset reference. Freezing checks the result against the manifest's research-row payload and the process-local guard. Restoring recomposes first, regenerates the snapshot, and requires exact canonical equality.

`runner_artifacts.py`, `bundle/1`, the old snapshot discriminator, and old result/archive readers are unchanged. The old snapshot reader rejects this new format. No hosted completion handler or capability currently accepts it. There is no permission to execute a restored financial forecast.

## Private immutable archive

`export_dataset_archive(reader, new_path)` and `extract_dataset_archive(path, new_directory, expected_root=...)` implement `atlas-dataset-ustar-v1`.

The first member is `manifest.json`, followed by `parts/<componentId>/<ordinal>.bin` in manifest order. Only exact type-0 USTAR headers with mode 0600, zero owner/group/time and no extension fields are accepted. The format rejects duplicate/out-of-order/unknown paths, links, PAX/GNU extensions, nonzero padding, truncation and anything after exactly two EOF blocks. Readers check file type and byte limits before body reads. Extraction creates a private 0700 sibling directory, verifies all parts, fsyncs, and atomically publishes with no replacement. A concurrently created destination is preserved. Export also stages privately and publishes without replacement.

Archive integrity reports `transportVerified:true`, `sourceAuthorityVerified:false`, `financialRecomputed:false`. A subsequent independently authorized `restore_dataset` is required for actual arithmetic/lineage reconstruction. This is not an independent financial-formula audit; it deliberately reuses the production financial core. No PDF, upstream wire response or provider rights are invented when absent from the frozen source.

## Resource profile

| Boundary | Hard ceiling |
| --- | --- |
| Securities / market rows | 50 / 110,000 |
| Financial packages / combined package bytes | 8 / 24 MiB |
| Market bytes / joined rows and snapshot | 24 MiB / 24 MiB each |
| Entire closure, including manifest and all duplicate derived evidence | 64 MiB |
| Manifest / individual part | 256 KiB / 512 KiB |
| Parts / components / graph depth | 256 / 32 / 3 edges |
| Registry entry / sum of original registry bytes | 256 KiB / 32 MiB; also within parent budget |

`DatasetProfile` can only lower these limits. Construction reserves the full manifest ceiling before computation/staging; this is intentionally more conservative than exact final-byte reader verification. Financial preparation receives the remaining shared budget after registry/manifest reservation. The final manifest and all original/derived components are counted again before callbacks. There is no automatic symbol truncation, dropping of missing rows, or loss of event dependencies to fit a budget.

The byte profile bounds serialized evidence, not Python RSS. Readback decodes bounded components in the local Python process. No 50-symbol/64-MiB performance or cloud-memory claim is made by the small synthetic tests.

## Acceptance and deferred integration

The committed tests use explicitly synthetic statement rows and a fake provider in memory; no network or provider API is invoked and no F estimator is fitted. Tests cover all 16 states, declared-unit preservation, reviewed-proof scope matching, float/int/signed-zero bytes, exact calendar/universe/dimensions, disjoint and overlapping sources, full missing diagnostics, re-signed forged outputs, missing/corrupt parts, typed and shared byte limits, strict USTAR failures, destination races, fresh-process reconstruction and the existing copy/JSON admission guard. A separate fixed full-year synthetic panel is used only to validate numerical fingerprints; the short input's training requirement is not reduced.

Not implemented: owner authorization, registry hosting, ready dataset lifecycle, HTTP routes, worker validation, durable dataset consumer, shared compute slot, hosted run binding, typed cloud archive downloads, independent stdlib dataset audit, GUI, provider fetching, F fitting or financial execution. These remain proposed in `DATASET_COMPONENT_IMPLEMENTATION_DRAFT.md`. This local slice does not turn `researchBindingEnabled` on.


Integrated acceptance on the v0.7 registry-correction base passed all 919 Python tests before the source-order regression was added. An actual previously frozen two-company input then exposed an unnecessary acquisition-order restriction, now covered by a reversed-order acceptance case and duplicate/foreign/malformed-symbol rejections. The resulting private real closure has seven components, 486 joined rows and a 1,067,520-byte archive; source market and prepared-state roots match the earlier evidence. Export, restore and financial snapshot reconstruction used no provider requests or model fitting. The original wire responses and annual-report PDFs are not included in this normalized-input closure.
