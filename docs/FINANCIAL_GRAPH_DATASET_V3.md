# Proposed compact financial source and thin numeric snapshot

This is the next local component design. Dataset/3 and financial bundle/2 are **not admitted, implemented as hosted formats, deployed or enabled**. Existing dataset/2 and financial bundle/1 retain their codecs, logical meaning and exact archived bytes. The measured full 50-security failure remains retained; it is not replaced by a compressed file.

Contracts: [dataset/3](../contracts/research-dataset-v3.json) and [financial bundle/2](../contracts/financial-bundle-v2.json). The existing physical 64-MiB source closure, 24-MiB joined/snapshot limits, row limits and 500-MiB disk reserve stay. The new layout addresses duplication, not an increased allowance.

## 1. Prepared state graph

Store each exact dependency and calendar object once, addressed by SHA-256 of the existing finite canonical financial JSON. Each event keeps its exact original scalar result, original lineage hash, ordered dependency references and optional calendar reference. Assignment intervals retain exact event references and original order. Original raw packages and separately authorized registry records remain unchanged.

Do not repeat daily prepared panels. The graph declares ordered securities, sessions, states and column names. For each symbol/session, the single covering assignment determines each state event. The exact conversion is the existing preparation conversion: `float(decimalValue)` and original available date for an `ok` result, otherwise `null`; reject underflow/overflow just as before. Reconstructed rows have the original row and field order. Every event and its expanded result must reproduce the original hashes. The complete logical prepared payload is hashed through a streaming encoder, including the exact original provenance, coverage and ordered assignments/events.

Dictionaries are closed, unique and sorted. References have depth one. Unused entries, dangling references, duplicate IDs, invalid assignment overlap/gaps, nonmatching event/lineage hashes and future availability fail. Dictionary hashes alone do not establish source authority: original input/pack/prepared/calendar roots and out-of-band registry bytes must all match a fresh source recomposition before F.

The physical closure remains at most 64 MiB. Each prepared component remains subject to the existing 64-MiB logical prepared limit and 32-MiB expanded event limit. At most one component's expanded canonical stream is processed at once; it must not be collected into a list/string for all inputs. Retained canonical payloads are capped at 64 MiB. Total streamed work is bounded by eight financial inputs, reference/cardinality bounds and the 900-second child deadline; sequentially hashing two legal components does not require retaining their combined 67.77 MB. This separation must be tested and measured, not asserted from the file-size saving.

## 2. Exact column table and thin snapshot

Replace repeated row key names with a declared column table. Numeric columns preserve finite JSON integer/float tokens, signed floating zero and null. String/date/security columns use first-occurrence dictionaries and integer indices; no arbitrary objects are allowed in a cell. Column lengths must equal the exact row count; the row order remains unchanged. A streamed canonical reconstruction must match the declared logical row-array hash and byte length.

The dataset stores this numerical component with its complete logical research provenance. Financial bundle/2 keeps an actual column table in its thin numerical snapshot, plus the frozen numerical fingerprints, dataset/3 reference and compact provenance/commitment. It does not fetch replacement values by URL and does not rely on a remote reference as if it were frozen input. The separate dataset archive retains raw statements and source graph.

Both physical and logical joined-document limits remain 24 MiB. The early prototype measured the 50-security numeric row array as 24,802,815 bytes before the existing panel numeric normalization. That is not the final model input oracle. The actual normalized row array is 24,855,215 bytes and its complete joined document is 24,923,037 bytes, leaving 242,787 bytes under 24 MiB. The new acceptance must account for the exact complete logical document. If it exceeds that existing limit, return failure and preserve the evidence; do not assume a column representation authorizes larger decoded input. Thin representation removes repeated transport/storage bytes but does not override the logical input guard.

Worker transport will eventually slice column values/indices into bounded pieces; part hashes and ordinal/count continuity must prove reconstruction. This is future transport integration. The initial Python component implementation will use bounded whole column tables, not change live routes or claim they are already streamable over the Worker.

## 3. Identity and compatibility

Keep exact source snapshot/manifest/package/registry bytes. Preserve original `inputRoot`, `packRoot`, `preparedRoot`, `calendarRoot`, logical `marketRoot` and `financialDatasetRoot` only after independent recomputation. New physical layout means new component roots and dataset root. New snapshot layout means a new bundle ID. Forecast artifact IDs may coincide only if the resulting actual forecast bytes coincide; no identity is copied to conceal a difference.

Legacy readers reject the new format. A future runner needs both new dataset and transport capabilities and an explicit new admission. Existing root mappings must not be silently redirected from dataset/2 or bundle/1. No release flag is included in this component cycle.

## 4. Vertical acceptance sequence

1. Build graph/column encoders and strict decoders as isolated pure components. Use original one-security prepared bytes and the small hand-computable fixture as independent exact oracles. Reconstruct every panel cell, null, date, integer/float representation, signed zero, event order and original root. This does not fit F.
2. Keep negative fixtures for wrong dependency hash, duplicate/dangling/unused references, interval gaps/overlaps, bad date availability, forged roots, unexpected columns, unequal column lengths, oversized rows, nonfinite values, underflow and altered `1` versus `1.0`/`-0.0`. Decode must fail before claiming source admission.
3. Reuse the already retained 50-security synthetic raw packages without refetching or reinterpreting them. Produce a new explicit dataset/3 identity, preserve all 50 members/16 states/366 calendar days and meet the unchanged physical and logical limits. Record full bytes, streamed expansion accounting, peak RSS and elapsed time.
4. In one child, recompose authenticated sources and execute the same automatic fundamental F with eight predeclared candidates, two inner/two outer folds, refit threshold 20 sessions and factor-free baseline. Preserve every terminal forecast/tail, diagnostics and callable final F even if no-change wins.
5. Save distinct new result/source archives. An independently implemented stdlib verifier must check graph/table expansion and all original logical roots without importing the engine. Only a complete verified 50-security result closes this capacity gap. HTTP, owner isolation, browser and release are later separate gates.

The measured projected event graph is approximately 15.34 MB versus 44.92 MB expanded; removing the 22.38 MB repeated prepared panels is a second saving. These are exact-data lower-bound measurements, not proof the final format, decoder or complete 50-security research fits the budgets.

## Pure-component acceptance, 2026-10-08

The isolated codecs are implemented under `engine/atlas_quant/research_dataset/graph_v3/`. The graph also stores the exact original package `selection`, which is part of the original prepared-root body. Validation separately checks the caller-pinned original prepared root and caller-pinned full prepared-payload SHA. It does not infer source authority from the graph.

Twenty-five pure component tests cover exact canonical streams, original hand-fixture payload/panel/root reconstruction, numeric type and signed-zero preservation, first-occurrence dictionaries, missing-key/null distinction, nonfinite/underflow/bool rejection, changed/unused/dangling/duplicate references, false roots, future availability, assignment overlaps/gaps, unexpected columns and legacy rejection. Together with the automatic financial profile tests, 27 tests passed. The preserved one-security source becomes a 173,504-byte graph versus its 758,999-byte original prepared payload, with exact reconstructed panel bytes.

A new supervised component acceptance used the **unchanged retained 50-security artificial source** from the failed dataset/2 attempt. No statements, state, securities or dates were removed; no provider or F was run. Two original prepared payloads and their exact prepared roots were reconstructed and all 13,100 daily rows checked. Results:

| Measurement | Bytes / time |
| --- | ---: |
| Two complete prepared graphs | 15,919,030 bytes |
| Exact numeric column table | 5,060,771 bytes |
| Prototype joined document before legacy numeric normalization | 24,870,637 bytes |
| Prototype remaining bytes (not final model input acceptance) | 295,187 bytes |
| Measured physical components plus full 256 KiB manifest reserve | 28,720,595 bytes |
| Child peak RSS | 555,859,968 bytes |
| Child / supervisor wall time | 10.17 / 11.58 seconds |

The prototype joined document SHA is `b48e2df426902027cbbb5b438571d300490e19cc4f61f2718f131ff9312e1a69`. The source-closure acceptance below found that this prototype did not apply the final market numeric normalization to its measured rows. Its bytes/hash are retained as historical component evidence, not as a successful legacy 50-security dataset identity. Private evidence is at `private/financial-graph-components-50-20261008-01`. The supervisor actively enforced 900 seconds, 3 GiB sampled RSS and 500 MiB disk reserve; it exited 0 with no stop reason. The previous failed source evidence remains intact. `benchmark-financial-graph.py` records both the declaration and measured process evidence.

At this recorded stage the acceptance was `PASS_COMPONENTS_ONLY`: no complete manifest, raw-source recomposition, thin snapshot, same-child F or new archive audit had run. The later source-closure acceptance below advances only the source gates. The measured component-byte sum includes the full manifest reservation but is not a claim that a complete new dataset archive was built.

## Next source-closure implementation boundary

The next separate local layer should provide `GraphDatasetReader` and `build_graph_dataset`, without changing legacy `DatasetReader`. They must reuse the existing original-snapshot scope validator and independently authorized package/calendar registry resolver. Sources stay sorted by pack root with exact nonoverlapping security/state ownership. A new manifest commits to source package bytes, source-origin bytes, graph bytes, column-table bytes and original logical roots; its new physical dataset root never impersonates an old dataset root.

A fresh `restore_graph_dataset` must re-run the financial preparation formulas from each original package under the existing per-input preparation/audit limits, one package at a time. Its computed prepared root and payload SHA must equal both the source descriptors and graph expansion. Only after all packages and the original market projection match may it produce the numerical rows and logical provenance. Reconstructed source data must match the entire column-table rows hash and the complete logical joined-document hash/length under 24 MiB. Raw package scope/calendar and original numerical conversion rules remain authoritative; graph-declared dictionaries are never a substitute for this recomposition.

A source-only restoration result should keep `modelAdmissionRegistered: false` initially. A later explicit new-profile admission can issue the existing process-local financial input capability only after this new validator has completed. It must not invoke the old dataset/2 validator with a higher budget, patch old provenance to skip admission, or deserialize a pre-authorized Python object into a child. The eventual child will restore source and fit F in the same process, with a new job identity and unchanged supervisor protections.

The thin snapshot constructor should accept this completed local source closure and build the proposed column snapshot. Its complete logical input envelope, not merely the row array, must be checked against 24 MiB. Result bundle/2 and dataset/3 must remain distinguishable from their older parsers, saved as separate new archives, and verified by a stdlib implementation that does not import these graph codecs. The 50-security F run is deferred until these source and snapshot gates exist; no component-only result opens production admission.


## Complete local source closure, 2026-10-08

`graph_v3/{manifest,source,dataset}.py` now implements the separately versioned closed component graph, an immutable bounded reader and fresh raw-package build/restoration. Every source is resolved from independently supplied exact registry bytes; every financial formula is rerun, its original prepared root and exact prepared-payload SHA checked, and only one expanded package is retained at a time. The compact graph supplies the reconstructed panel used for joining. Original source package/market bytes remain unchanged. A copied or rehashed coverage claim fails the fresh comparison. Result frames remain unregistered for model admission.

Actual old-v2/new-v3 small-fixture joined rows, complete provenance, schema and coverage are byte-identical. Fifty-three targeted graph, source and original snapshot-view tests pass. Legacy readers reject dataset/3 and model-input preparation rejects the source-only result.

The first full-scope build (`private/financial-graph-source-50-20261008-01`) is retained as a failed oracle comparison. Its complete source was valid, but the earlier component prototype computed its joined identity using market `vol`/`adj_factor` integers after separately validating a float-normalized frame. Actual legacy-v2 composition uses the normalized output. The prototype script is corrected; its old evidence is neither overwritten nor retrospectively presented as a valid legacy dataset.

The second explicit attempt (`private/financial-graph-source-50-20261008-02`) uses a separately saved normalized oracle derived from the retained old component values and the existing legacy panel semantics. It records 26,200 int-to-float logical cell conversions; **raw market input bytes are unchanged**. The old 50-security dataset/2 never passed its source-byte guard, so no successful old 50-security dataset/root is asserted.

Both fresh build and second-process raw-source recomposition passed with all 50 securities, 13,100 rows, 16 states and 366 inclusive calendar days:

| Measurement | Result |
| --- | ---: |
| Physical complete closure | 28,924,215 bytes |
| Manifest / components / parts | 13,767 bytes / 10 / 60 |
| Complete normalized joined rows plus provenance | 24,923,037 bytes |
| Remaining under unchanged 24 MiB | 242,787 bytes |
| Fresh build wall / peak RSS | 17.80 seconds / 680,263,680 bytes |
| Separate recompose wall / peak RSS | 17.29 seconds / 739,573,760 bytes |

Dataset root: `cca665750bd7ba9d5eacc7ac7b0e54418397c35bf7f22fbb1981349d3cad3172`. Logical joined SHA: `8077543843d06f7f575f606da27ec4fa378155be5e716435aed9cf4ea5ff072b`. The unchanged source data fingerprint is `7153e3436e2aa3aa2997d115c6b09799c775b285b9b5f0672c6cfd5846104975`. The supervisor enforced 900 seconds total, 3 GiB RSS and the 500 MiB disk reserve and exited without a stop reason.

`sourceAuthorityVerified=true` here means only independently authorized registry bytes plus recomputation of the exact frozen package. All sources remain artificial (`synthetic=true`); original publication/PDF authenticity, full filing history and revision-time verification remain false. Provider calls, model fits and executions are zero. Thin snapshot, model admission, full F, result/source dual-archive audit and hosted production remain separate uncompleted gates.


## Explicit local F admission and thin snapshot

The separate local profile is `financial_fundamental_graph_auto_50_v1`. It accepts only dataset/3, the exact frozen scope, fundamental/auto/asset-price, explicit execution disabled, predictor factors without temporary bindings, at most 50 securities/16 factors/366 inclusive days, two inner/two outer folds and a refit threshold of at least 20 full calendar sessions. It reuses existing mathematical rules; it does not route graph inputs through the old source validator or relax old byte guards. No Worker or runner-claim registration is included.

`graph_v3/snapshot.py` restores every source in the calling child and only then registers that exact frame in the existing weak process-local registry. A source-only result or a forged `model_admission_registered` boolean cannot freeze research input. The snapshot contains actual `exact_column_table/1` values and preserves the research fingerprint, source fingerprint and financial commitment. Both its physical encoding and its complete row-expanded form, including every metadata field, must be under 24 MiB; the source joined document has its own unchanged 24 MiB guard.

`financial_bundle_v2.py` defines a separate financial-bundle/2 manifest and codec. `snapshotColumns` maps to `/numericInput/columns`. Each complete column descriptor and values/indices vector is a bounded record; chunks split between column records, never through a number. An individual column still must fit the unchanged 8 MiB item guard. Forecast/report/coverage collections retain their original canonical representation and forecast identity. Legacy financial-bundle/1 and ordinary bundle parsers reject the new format. Directory reads and new no-replace USTAR exports preserve exact bytes; source graphs remain in their own archive.

Seventeen snapshot tests and eleven bundle tests cover explicit profile boundaries, independently authorized source restoration, source-only/forged-admission refusal, altered values/provenance/commitments, complete logical-size checks, codec/layout tampering, old-parser rejection and new archive/directory boundaries. A genuine small artificial study was run on actual old-v2 and new-v3 inputs: all frozen forecast bytes were identical, with exact input fingerprints and provenance. These are engineering/numerical parity checks, not a validated trading edge.

The small retained paired audit fixture is `private/graph-bundle-small-audit-20261008-01/test_retained_two_archive_fixt0`. It contains `financial.tar`, `dataset.tar`, both exported directories and separate original caller registry pins. An independent stdlib paired audit and the newly identified full 50-security F job remain required before claiming that end-to-end capacity is accepted. Production and hosted flags remain absent/disabled.


## One full-scope local F run and retained audit boundaries

After the independent small paired audit passed, new job `094ca26b-91cf-4d6a-bb95-befd96440ae8` ran once against the unchanged 50-security source root `cca665750bd7ba9d5eacc7ac7b0e54418397c35bf7f22fbb1981349d3cad3172`. Its predeclaration, progress events, exact result/source archives and every failure remain in `private/financial-graph-auto-50-20261008-01`. The caller's shared private compute lock excluded concurrent heavy jobs; a parent actively enforced 900 total seconds, 300 seconds per fit, 3 GiB RSS and 500 MiB free disk. No provider was contacted.

The child freshly recomposed all sources and verified the complete snapshot before F. It then ran the same nested/sequential F code with all eight declared candidates and a separately reselected factor-free baseline. All 2,050 main predictions and 2,050 baseline rows remain: 1,750 mature and 300 unavailable-future tails. Nineteen factor/derived inputs produced all 171 empirical joint tables, full factor statistics and three final callable F artifacts. The 105 actual fit events include one early candidate failure: primary outer-fold `elastic_net:0` did not converge, and its invalid trial/reason was retained rather than deleting or rerunning it. Final selection retained all eight candidates. `no_change` won; status is `NO_VALIDATED_FORECAST_EDGE`, and there are no trades.

| Measurement | Result |
| --- | ---: |
| Child / supervisor elapsed | 49.30 / 50.22 seconds |
| Child peak RSS | 574,210,048 bytes |
| Physical thin snapshot | 5,183,350 bytes |
| Full expanded snapshot including all metadata | 24,925,386 bytes |
| Financial / source USTAR | 8,948,736 / 28,959,232 bytes |

Bundle ID is `5fdc2c64d229a96a28c0c5cfa470b5fb2083193bf5bcf631585d559463feff73`; forecast ID is `1c8b5993b206828ba9933591b48d648476c5304836cc8360f2addeac2c85f02a`. The independent stdlib paired audit checked 710,319 result conditions and 3,847,451 source conditions, with exact original external registry/source pins and zero numerical-identity error. It did not recompute F, statistics or source formulas. A first audit invocation using the system Python 3.9 failed because the existing auditor uses `zip(strict=True)`; that failure is retained. The same bytes passed unchanged under modern Python with `-S` and no engine imports, without changing the auditor or oracle.

A separate supervised process then extracted the saved source TAR and freshly recomposed every source formula and the research input fingerprint. The regenerated snapshot matched exactly: 27.90 seconds, 643,645,440-byte peak RSS, no new F/provider run. Its receipt is under `fresh-recomposition/`. An after-run code attestation and the actually executed new runtime/script are preserved under `executed-code/`; this is explicitly not a prerun signature, and later source changes never represent a new fit of this job.

**Hosted gate remains blocked.** A separate cross-format adversarial review found that old coverage audits can accept coordinated removal of a security's main predictions, baseline and declared plan while the full source snapshot remains. The successful checks above prove the stated exact-byte/source/numerical identities, not independent completeness of the forecast domain. New graph result verification and the independent paired auditor must derive expected terminal origins from the frozen source scope/calendar and declared research clock, then reject any missing security/date regardless of a self-consistent edited plan. The original 50-security outputs must stay unchanged; this gap is fixed and tested against them and adversarial derived copies without fitting again.

## Source-derived forecast domain guard

The graph Python reader now regenerates the expected terminal domain from the freshly restored, admitted source frame and the validated declaration; it does not trust the saved `plannedOrigins` collection. The exact existing financial `build_samples` and `forecast_origins` rules supply all symbols, observation cadence, holdout boundary, entry/target sessions and tail rows. Exact target definitions and main/baseline/plan rows must cover that full domain; planned input validity is recomputed too. Eight no-fit tests reject coordinated missing, duplicated or altered origins, dropped tails, changed targets, changed input validity and absent baseline.

The original 50-security result passed this later no-fit verification in a fresh process: 2,050 expected origins across 50 securities, 40.804 child seconds and 724,205,568 peak RSS bytes. The new receipt is `private/financial-graph-auto-50-20261008-01/fresh-recomposition-source-coverage/recomposition.json`. Original result/archive bytes and the original fit attestation remain untouched. Independent stdlib full-domain verification is still a separate outstanding hosted gate.

A further runtime correction uses the common `CAPACITY_MEMORY`, `CAPACITY_TIMEOUT` and `CAPACITY_DISK` error codes so automatic candidate selection propagates global resource exhaustion immediately rather than counting it as one failed candidate. A no-fit injection after source/snapshot reconstruction verifies disk exhaustion escapes selection. The original 50 run's parent resource protections were active; this later correction is not a rerun or retrospective claim about that executed source.

## Disabled hosted runtime candidate

The Python graph research runner now declares the four exact capabilities only when `financial_graph_research_enabled` is true. Dataset/3 source input, bundle/2 result output and their encrypted spools have independent format and cryptographic domains. Fresh raw restoration and complete source-derived forecast-domain checks precede the final persisted result marker. The parent independently samples 3 GiB RSS, 500 MiB disk reserve and a 300-second active-fit budget; the existing process wall/stop/lease supervision remains. Delivery recovery uses existing encrypted bytes and never refits. Source and result rejection evidence is retained by the research runner in an encrypted quarantine; server machine codes are bounded and message/body text is discarded.

Graph composition runs as an explicit **sixth service**, `python -m atlas_quant.graph_dataset_runner --config /absolute/private/config.json`, with its own `graph_dataset_enabled:true`, `graph_dataset_delivery_dir`, `research-dataset-graph/1` capability and `dataset_graph_compose` kind. The independent graph queue does not run inside the legacy composition poll loop. It retains the 600-second source-job deadline, independent durable claim identity, authenticated input/output bytes, exact same-owner evidence, parent RSS/disk supervision, and the common private compute lock. Private config validation requires the lock; neither old Python nor Worker flags implicitly enable this service.

Forty graph/legacy source-service tests pass, including one small source-only spawned child, complete original component equality, all four lost-ACK points, exact version/lease rejection and explicit disabled flags. No models or providers ran in these service tests. These are local transport/runtime tests, not hosted HTTP, deployment or long-running service acceptance. Production graph flags remain disabled; independent source/result audit and real isolated HTTP/browser acceptance remain separate requirements.

The independent runtime review found and fixed two graph-specific gaps: Python equality previously accepted result transport version `2.0`, and the graph composition's final integrity pass ran after the earlier resource sample. The graph client now requires an integer version, and publication checks peak RSS, the disk reserve and the original deadline callback after full integrity validation and before writing the final manifest. Graph composition also has its own encrypted rejection marker. After a rejected publication the claim is settled, but exact source cache, input metadata, result parts and manifest remain; a crash after the rejection marker cannot trigger republication or recomposition. The legacy dataset service keeps its existing format and cleanup semantics.

Thirty-six targeted service/recovery tests and fourteen source-authority adversarial checks passed after this review, without a model fit or another spawned source job. Self-consistent registry copies, rehashed coverage, changed full joined metadata and forged model-admission booleans remain rejected by fresh authorized-source reconstruction. These are Python/local properties; owner/lease fences on the new hosted routes still require separate HTTP acceptance.

## Independent full-source domain acceptance, 2026-10-08

The previously recorded independent coverage blocker is now closed for the unchanged local 50-security bundle. The updated standalone auditor derives all 50 targets and 41 terminal origin dates from the frozen source calendar and declared research clock, including all 300 tail rows. Both main and baseline contain the complete 2,050-row grid; target definitions and the 2024-11-05 holdout boundary match. It passed 710,319 result and 3,859,466 source checks against separately provided original source and registry pins, without importing the engine, fitting a model or calling a provider.

Three independent adversarial copies remove half the assets, one complete origin date, or every unavailable-future tail, while rebuilding all inner hashes and the aligned forecast/baseline/plan collections. Each passes transport/self-consistency but fails the independent source-domain gate. The original manifest, eighteen chunks and both archives retain their twenty-one original hashes. Audit and attack receipts are in `../atlas-quant-graph-audit/private/graph-source-domain-20261008-01/`; the evidence contracts record their hashes. The original run and earlier audit/failure records remain preserved.

This independent pass checks scope, clocks, exact columns/provenance and numerical forecast identities. It does not recompute input-validity masks, feature values, financial formulas, F or research statistics, and it does not independently authenticate the user's original requested strategy or provider/PDF history. The separate fresh engine source-domain verification above recomputed input validity; these are distinct evidence claims. There is no new fit, new strategy edge or production acceptance. The remaining hosted gates are real isolated source/result HTTP ownership and lease fencing, interrupted delivery and terminal rejection recovery, paired archive downloads with independent pins, and browser acceptance. Both graph feature flags stay off pending that integration.
