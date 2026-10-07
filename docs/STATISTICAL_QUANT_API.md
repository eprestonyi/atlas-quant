# Statistical Quant 0.4 API and persistence

This is the implementation contract for the independent `statistical_quant` workspace. The configuration is [STATISTICAL_QUANT_SCHEMA.md](STATISTICAL_QUANT_SCHEMA.md). Base path is `/quant/api/statistical-quant`. Historical `/strategies`, `/runs`, factor/data/universe/code endpoints remain available; historical reports never receive invented forecasts.

JSON errors use `{error:{code,message}}`. Private routes use the existing HttpOnly workspace cookie from `/quant/api/session`, enforce same-origin writes, and derive ownership on the server. A foreign or unknown identifier returns 404. Strings supplied as owner identifiers never alter ownership. The API does not execute saved Python.

## Module and configuration discovery

Public GET routes:

| Path | Result |
|---|---|
| `/workspaces` | One implemented statistical workspace; market making, derivatives and structural research are explicitly unavailable |
| `/modules` | Typed versioned modules with input/output types, capabilities, data dependencies, PIT policy, mechanism and provenance |
| `/recipes` | Concrete finite configuration recipes, explicit module references and editable configuration patches |

Modules and recipes accept `q,stage,availability,page,pageSize`. Page is 1–10,000, pageSize is 1–100. Stages are target/state/model/validation/hedge/risk/execution/cost. Response is `{schemaVersion,items,total,page,pageSize,counts,definitionsAreNotExperiments:true}`. The response also publishes limits of 50 symbols, 32 factors, 110,000 input rows, 25,000 complete forecast rows and 24 MiB per full artifact. Increasing observationDays or narrowing symbols/windows reduces forecast origins; no automatic subset or silent truncation occurs. Counts distinguish atomic modules and configuration recipes; they never infer completed experiments or alpha from the number of definitions.

A module includes `{id,version,name,stage,inputType,outputType,configPatch,patchSemantics,availability,capabilities,requiredData,pointInTime,parameters,provenance}`. Parameter names are complete configuration paths such as `model.trainWindow`. `merge` patches merge the declared sections; `append_factors` adds the supplied exact DSL definitions while preserving user-selected factors. Recipes use `merge_replace_factors` and carry a complete chosen factor set. Switching target type should replace its incompatible prior basket section, not retain hidden parameters. `configurationRequired:true` means explicit basket members/quantities or other selections are still required.

Catalog availability means a module exists; it does not prove the selected companies have sufficient actual data. Event recipes use changes in PIT fields, not repeated nonzero financial levels disguised as announcements. `runCount:null` means execution counts are not inferred from a recipe definition. Factor-score-only models are excluded from the new workspace.

The existing `/research-presets` response now provides schema-2 entry points under `strategies`; these are introductions to the larger composable registry. Previous v0.3 residual templates are under `legacyResidualStrategies` with historical metadata. Older long-only templates remain `legacyStrategies`.

`GET /summary` is private and returns actual counts for the current owner: experiments, forecastArtifacts, completedExecutions and comparisons. Definition counts never stand in for these measurements.

## Experiments

| Method / path | Input and result |
|---|---|
| `GET /experiments` | Paged `{items,total,page,pageSize}`; current owner, unarchived |
| `POST /experiments` | `{strategy}` → 201 `{experiment}` |
| `GET /experiments/:id` | `{experiment,runs,forecasts,executions}`; the latest 100 of each linked type |
| `PUT /experiments/:id` | `{strategy,version}` → `{experiment}`; optimistic conflict is 409 |
| `POST /experiments/:id/copy` | `{name?}` → a new experiment with parentId; no copied completion claims |
| `POST /experiments/:id/run` | `{version,dataSource,dataset?}` → 202 `{experiment,job}` |
| `GET /experiments/:id/export` | Download current configuration and linked manifest |
| `DELETE /experiments/:id` | Soft archive; preserves versions, runs and immutable artifacts |

An experiment is `{id,name,version,strategy,parentId,archived,createdAt,updatedAt}`. Save/update bodies are capped at 200,000 UTF-8 bytes. Configuration CAS and immutable version insertion commit in one D1 batch transaction; a failed history insert rolls back the head and a losing concurrent writer creates no extra revision. Schema 2 uses strict unknown-key/type validation; unsupported risk settings cannot be silently dropped. Run input uses demo/upload/tushare and the existing 26 MiB envelope, 24 MiB data, one active job per owner, twenty daily submissions and thirty global queued/running jobs. Saved universe selection hashes are re-resolved before a new prediction run. The queue projects trusted resolver output to exactly catalogSnapshot `{hash,asOf,historicalMembershipVerified:false}` and revalidates the bound configuration before storage. Public schema-2 input remains strict. When reading early server-stored candidates only, four known resolver summary fields (`source,securityCount,universeCount,missingIdentityCount`) may be validated and projected away; other unknowns fail and complete immutable artifacts are never rewritten. Explicit source membership and subsets remain auditable.

The old `/runs` and operator enqueue paths also recognize schema 2 and create a linked experiment automatically, so no new-mode job bypasses artifact persistence. Job polling, complete report download and cancellation still use `/quant/api/runs/:id`, `/:id/export`, and `/:id/cancel`. Jobs identify `jobKind:'forecast'|'execution'` and experimentId. A saved experiment is not a completed research run.

## Immutable forecasts and model versions

| Method / path | Result |
|---|---|
| `GET /forecasts` | Owner-isolated paged forecast manifests |
| `GET /forecasts/:id?offset=0&limit=50` | `{forecast,artifact,offset,limit,preview:true}`; limit 1–200 |
| `GET /forecasts/:id/download` | `{forecast,artifact}` containing the complete private forecast artifact |
| `GET /model-versions` | Up to the latest 100 owned model-version manifests |
| `GET /model-versions/:id` | Exact owned model-version manifest |

`forecast` is a metadata manifest: id/forecastArtifactId, jobId, experimentId, modelVersionId, predictionConfigHash, dataFingerprint, rowCount, targetCount, modelFitCount, metadata and createdAt. No R2 keys, runner lease or credentials are exposed. The immutable object has one source experiment, while experiment details use quant_runs links so identical artifacts produced by another experiment remain visible in both histories.

`artifact` follows the numerical schema: sourceStrategy, immutable artifactId, predictionConfigHash, dataFingerprint, all forecast rows, targetDefinitions, modelFits and diagnostics. Engine output must have `rows.length===totalRows` and `truncated:false`; an oversized complete product fails instead of silently dropping origins. Manifests and previews expose aggregate diagnostics and explicit detail counts, excluding per-origin factor ablation rows, fit histories, daily losses and unbounded per-target diagnostics. Complete downloads preserve all of those audit details. The preview alone slices rows and their associated target/model definitions, retaining totalRows and `truncated:true` when appropriate; hedge fitting detail is available in the complete download. Full download includes invalid, untraded, zero-edge and immature predictions too.

Forecast-only jobs may complete with metrics null and empty equity/trades. Actual trade rows must reference an existing forecastId. A successful forecast requires its matching frozen dataset already uploaded. Dataset and complete artifact are separate private R2 objects, with content hashes and D1 ownership/reference records. Download and replay recompute SHA-256 of the stored text and compare the D1 digest before returning it; missing or changed objects fail closed. The source data is not embedded in public catalog metadata or the report. Model versions retain fitted-version identity, source artifact, declared model configuration and finite selection metadata.

## Reusing forecasts and comparing results

`POST /executions` accepts `{forecastArtifactId,execution?,portfolio?,costs?}`. It checks the source artifact belongs to the same workspace, overlays only these three allowed sections, validates the full resulting configuration and queues an execution job. Changing features, model, target, research dates or snapshot is not allowed. Optional risk limits refer only to selected factor IDs. Return is 202 `{execution,job}`.

`GET /executions` returns a paged owned execution list. `GET /executions/:id` returns `{execution,result}`; `/:id/download` downloads that complete result. Execution configuration, source forecastArtifactId, experimentId, status and result/error metadata remain linked. The runner retrieves the original artifact and frozen input separately under its current lease, never refetching provider data or re-estimating a model. Current-universe membership changes do not invalidate a replay of the originally frozen research snapshot.

`POST /comparisons` accepts `{name?,kind:'forecast'|'execution',members:[id,...]}` for 2–8 different owned products. Execution comparisons require completed jobs. `GET /comparisons` lists saved comparisons for the current owner. `GET /comparisons/:id` and `/:id/download` preserve the references and comparison snapshot. Forecast comparisons report target, universe/window and data-fingerprint compatibility; execution comparisons explicitly report whether the same forecast artifact was reused. Incompatible products may be inspected together but are not called a controlled factor/fee comparison. `independentlyValidatedAlpha:false` is separate from numerical or API success.

## Private runner transport

These routes remain under `/quant/api/runner` and require the independent operator bearer credential, not a browser cookie:

- `POST /claim` accepts `{engineVersion,requestId?}`. New runners persist a lowercase UUID `requestId` before transport. D1 reserves its job and changes queued→running in one transaction. Repeated or concurrent requests with the same ID return the same job and lease, including after response loss; the retry does not extend that lease. Every request with an ID echoes `claim:{requestId,status,jobId?}` where status is `running`, `empty`, `completed`, `failed` or `cancelled`. Running returns the ordinary job payload; terminal/empty returns `job:null`. Empty requests reserve nothing. Expired leases are terminal failures and never revived. Maintenance blocks new reservations but permits existing claim recovery. Requests without an ID retain legacy behavior. Compact receipts remain with job history rather than expiring while a runner may hold an offline encrypted intent.
- `claim` only offers schema-2/execution tasks to `engineVersion >= 0.4.0`; older runners can still receive legacy work behind newer queued tasks. Claim metadata is small and does not combine the two large replay objects.
- `POST /snapshot` accepts `{id,leaseToken,snapshot:{schemaVersion:1,rows,provenance,dataFingerprint,...}}`, maximum 24 MiB plus the small request envelope. The hash covers the entire snapshot including provenance and calendar. Identical retries are acknowledged after completion; changed content under the same job is rejected. Canceled/failed deliveries receive `terminalDiscard:true` without resurrecting the job.
- `POST /replay` accepts `{id,leaseToken,kind:'dataset'|'forecast'}` and returns `{snapshot}` or `{artifact}`. Only a currently valid execution lease can read these products. Separate responses avoid a combined oversized claim payload.
- `complete` retains exact-payload idempotency and cancellation races. Forecast completion checks snapshot/artifact fingerprints; an exact retry also repairs any missing artifact indexes after an interrupted storage step. Execution completion must preserve the source artifact exactly.

The runner keeps encrypted local delivery and snapshot spools, uploads snapshot before report completion and deletes local spool entries only after acknowledgment. A transport or permanent-data error never falls back to fresh provider history or synthetic replacement.

## Additive migration and verification

Apply [0003_statistical_quant.sql](../edge/migrations/0003_statistical_quant.sql) and [0004_runner_claims.sql](../edge/migrations/0004_runner_claims.sql) before deploying the new Worker; applying them repeatedly is safe. The latter adds `runner_claims` durable request-to-job receipts. The same schema is included in `edge/schema.sql` for new local databases. New tables are quant_experiments, quant_experiment_versions, quant_runs, quant_model_versions, quant_forecast_artifacts, quant_executions and quant_comparisons. Existing tables and historical rows are not removed or rewritten. Temporary upload cleanup does not remove a separately referenced forecast snapshot. Unreferenced inputs from failed/canceled jobs are removed after 30 days in bounded scheduled batches; completed forecast inputs remain retained with their artifacts.

The shared [worker-source.mjs](../scripts/worker-source.mjs) builds actual ES modules for production and Miniflare tests. Static web subdirectories retain paths and MIME types; tests/docs/private directories are excluded. A graph revision changes entry URLs, while child JavaScript uses no-cache plus content ETags. Binary assets round-trip as their original bytes.

`tests/statistical-quant.test.mjs` covers schema composition, additive migrations, owner isolation, snapshot prerequisites, full download, immutable execution replay, comparison and runner-version compatibility. `tests/statistical-quant-runner.test.mjs` runs the actual Python forecast and execution through the Worker, D1/R2 and JSON transport using explicitly synthetic data; it is separate from real-provider and production evidence. Shared JSON acceptance cases are in `tests/fixtures/statistical-quant-configs.json` for both languages.

`tests/runner-claims.test.mjs` exercises actual D1 transaction rollback, concurrent identical claims, lost-response retry, terminal/expired receipts, maintenance recovery and legacy compatibility.
