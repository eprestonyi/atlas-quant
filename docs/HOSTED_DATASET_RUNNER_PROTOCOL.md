# Hosted dataset runner HTTP protocol, revision 1

Default-off local implementation. The numerical dataset manifest belongs to the
source-core owner; this document fixes only its leased delivery envelope. Shared
machine constants/keys: `contracts/hosted-datasets-v1.json`.

All relative routes below begin `/quant/api/runner/datasets`. Existing operator
Bearer authentication is required. Raw PUT still uses Content-Type
application/json; no reserialization of supplied bytes. GET/PUT lease header is
`X-Dataset-Lease`. No token in a URL. All descriptor hashes are lowercase SHA256.

- POST `/heartbeat`: `{capability:'research-dataset/1',engineVersion,state,
  jobId?,leaseToken?,phase?}`. Idle returns `{ok:true,canClaim:boolean}` without a
  durable claim. Busy returns `{ok,leaseValid,cancelRequested,leaseUntil}`. Phases
  are in the JSON contract. Fixed deadline600s; lease120s; heartbeat20s.
- POST `/claim`: `{requestId,capability,engineVersion}` → `{claim:{requestId,
  jobId,status},job:null|{id,kind:'dataset_compose',planId,leaseToken,leaseUntil,
  deadline,inputUrl}}`. Exact durable UUID retries keep identity, including empty.
- GET `/jobs/:id/input`: `{job:{id,kind,planId,deadline},planRoot,
  plan:{profile,marketSource,financialInputs,marketCalendarRef,scope},sources,
  registry:{count,totalBytes,listUrl},limits}`. `marketSource` is the reviewed
  public DTO; financialInputs are exact root refs. calendarRef is server-derived
  from selected same-owner financial grants, never a client trust override.
- `sources.market={runId,bundleId,originalScope,manifest:{sha256,byteLength,url},
  snapshot:{sha256,byteLength,rowCount,parts:[{ordinal,start,count,sha256,
  byteLength,url}]}}`.
- `sources.financial=[{sourceId,inputId,preparationId,roots,calendarRef,proofRefs,
  package:{sha256,byteLength,parts:[{ordinal,startRow:null,rowCount:null,sha256,
  byteLength,url}]}}]`. These are original canonical package parts, not prepared
  JSON inferred from a summary. Consumer constructs FinancialSource from them.
- GET `/jobs/:id/sources/market/manifest`, `/sources/market/parts/:ordinal`,
  `/sources/financialN/parts/:ordinal`: identity/no-transform raw bytes with
  x-content-sha256 and content-length. Source manifest≤512KiB, original market
  chunk≤8MiB, reconstructed snapshot≤24MiB, package parts≤512KiB. Original source
  snapshot must validate in full before the explicit scope transformation.
- GET `/jobs/:id/registry?offset=0`: `{items:[{ref,kind,sha256,byteLength,url}],
  total,offset,nextOffset}` at most64. GET each fixed URL returns≤256KiB exact
  owner-authorized canonical registry bytes. Descriptors sum≤32MiB before reads.
- POST `/jobs/:id/publication`: `{leaseToken,datasetRoot,manifestText}` →
  `{publicationId,datasetRoot,missing:[{componentId,ordinals:[...]}]}`.
  manifestText is the canonical dataset/2 JSON string, with outer JSON transport
  escaping only; decoded UTF8 must hash to datasetRoot. Limit256KiB exact bytes.
- GET `/jobs/:id/publication?datasetRoot=...`: same resumable missing-part receipt.
- PUT `/jobs/:id/publication/:publicationId/parts/:componentId/:ordinal?datasetRoot=...`:
  raw≤512KiB body; returns `{ok:true,componentId,ordinal,sha256,byteLength}`. Different bytes conflict.
- POST `/jobs/:id/complete`: `{leaseToken,publicationId,datasetRoot}` →
  `{ok:true,status:'completed',datasetRef:{datasetId,datasetRoot,
  format:'atlas.quant.research_dataset',version:2}}`. Same completion recovers the
  same ID; cancelled/expired work cannot publish. No source ref rewrites.
- POST `/jobs/:id/fail`: `{leaseToken,error:{code,message}}` → `{ok,status}`.
  GET `/jobs/:id/status` with lease → `{job:{id,status,error},datasetRef:null|ref}`.
  Exact terminal reads/completion ACK recovery are permitted; no re-computation.

The consumer first checks metadata/descriptor limits, independently verifies raw
source/registry identities, rebuilds the original snapshot from its manifest,
derives the explicit view, re-prepares financial inputs, and calls the new pure
compose function. It freezes a complete output manifest before delivery. Partial
calculation cannot masquerade as ready. Provider access is absent.

F readers are a separate namespace `/quant/api/runner/research-datasets/:jobId`.
The live research lease and immutable `quant_run_datasets` row authorize:
`/input` (datasetRef + sourceEvidence + manifest descriptor +registry count),
`/manifest`, `/parts/:componentId/:ordinal`, `/registry?offset=...`, and
`/registry/:ref`. The provider-free F child reconstructs and registers the dataset
in its own process. Exact result transport fields come from
FINANCIAL_BUNDLE_TRANSPORT, never an invented edge-side variant.
