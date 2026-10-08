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

Both physical and logical joined-document limits remain 24 MiB. The current 50-security numeric row array is 24,802,815 bytes, leaving only 363,009 bytes before provenance/outer syntax. The new acceptance must account for the exact complete logical document. If it exceeds that existing limit, return failure and preserve the evidence; do not assume a column representation authorizes larger decoded input. Thin representation removes repeated transport/storage bytes but does not override the logical input guard.

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
