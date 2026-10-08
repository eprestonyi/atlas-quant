# Frozen financial dataset research runner

The existing outbound research runner can opt in to `atlas.quant.research_dataset/2`
with `financial_dataset_research_enabled: true` in its external private config.
The default is false. The claim advertises dataset/2, `financial_json_v1`, and
`atlas.quant.financial_bundle/1` together. This capability is not a production
feature flag: the edge also controls admission separately.

This slice accepts only `financial_snapshot_view_50_v1`, explicit forecast-only
schema 2, asset-price targets, fundamental family and Ridge estimation. Scope
must exactly match the frozen dataset. No provider or PCD credentials are added
to these jobs; the child receives no queue Bearer secret. Its source inputs are
already frozen. The inherited TUSHARE_TOKEN is removed in this child.

## Data and process boundary

`research_dataset_runner/client.py` reuses the bounded dataset transport but
permits only GET against `/runner/research-datasets/<job>/...`. Every component
and external registry read uses the same lease and dataset root. Advertised
URLs must equal the locally constructed path. Redirects, compressed responses,
changed lengths/hashes, expanded limits, duplicate pins, and malformed directory
pagination fail admission. All registry descriptors are admitted before reading
payloads. The source directory has up to 2,057 entries; its encrypted local index
retains only UUID, hash, and byte length, so repeated HTTP paths do not consume
component-part capacity.

`spool.py` saves exact raw parts and external registry bytes using AES-GCM, a
separate directory/magic/AAD domain, private permissions, atomic replacement,
and fsync. Sources are not pickled or sent through process pipes. The spawn
control envelope is capped at 256 KiB; the child gets only its small job and local
encrypted spool context.

`compute.py` restores the complete original market snapshot and exact selected
view, verifies external registry pins against the source closure, re-prepares
financial states, and fits F in the same child. Process-local admission cannot be
transported from the parent as a trusted Python object. The resulting typed
snapshot retains `1.0`, signed zero, nulls, and original provenance. A separate
financial output bundle contains the complete prediction rows and the pre-fit
coverage plan. The complete source dataset remains a separate attachment.

## Deadlines, host concurrency, and restart

Input download plus compute is capped at 600 seconds and the configured research
budget, whichever is smaller. Source reads heartbeat the lease. Cancellation,
stop, or the hard deadline ends the child. `compute_lock_path` must be the same
private lock for production research, financial preparation, and dataset
composition. Waiting for this slot consumes the original deadline. Normal
forecast fitting and execution replay also honor this slot.

A complete financial manifest is written after its chunks. If the parent dies
before saving the completion envelope, the original durable claim can recover
that complete manifest and deliver its exact bytes. With no complete manifest,
the same lease fails as `RUNNER_INTERRUPTED`; it never silently refits. Unknown
upload/completion outcomes retain both encrypted inputs and outputs. Only a
matching terminal claim receipt permits cleanup. Success uses the dedicated
financial completion route; errors use the existing generic completion route.

## Evidence boundaries

`test_research_dataset_runner.py` exercises actual spawned synthetic F,
source-byte and pin substitution, full claim/download/compute/delivery flow,
unknown terminal receipts, and both complete/incomplete crash recovery. Existing
runner, claim, and bundle tests remain separate regressions. HTTP acceptance and
production installation are additional gates. A forecast artifact or a successful
transport check is not evidence of predictive edge, profitable execution, or
verified accounting assumptions.
