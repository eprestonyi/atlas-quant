# Stage 1: explicit pair target kernel

This package is an unregistered, standard-library-only mathematical kernel for a
future Studio/low-level reproducible path. It has no model, estimator, provider,
HTTP client, product switch or automatic pair selection. It does not complete
Easy pair research or establish a pair compute capacity. The public contract is
[`contracts/pair-research-v1.json`](../../../contracts/pair-research-v1.json).

`prepare_price_input(source_domain, rows)` validates a local open/close projection
and hashes it. `declare_targets(price_input, pair_map, quantity_cutoff=...,
origins=..., horizon_sessions=...)` freezes the explicit signed quantities and
emits the complete member ledger. `build_targets(declaration, price_input)` checks
both identities and returns every declared pair at every declared origin.
`validate_price_input` and `validate_declaration` reject unknown fields, stale
roots, malformed members, duplicate rows/pairs, and out-of-domain values. They
return detached normalized copies and never mutate the caller's objects.

U is the entire frozen filter membership. T is the separately declared pairMap;
each target has exactly two distinct U members with opposite nonzero share
quantities. Shared legs, odd U, unmatched members and empty T are valid. An
unmatched member remains in U with `not_in_explicit_map`, never a fake return or
an invented statistical rejection. Duplicate unordered pairs are rejected even
if reversed, rescaled or renamed. Pair order and leg order are explicit identity
fields; the kernel does not silently orient a pair or normalize its quantities.
There is no candidate graph G at this stage.

At origin close, `S = sum(q * price)` and gross scale
`G = sum(abs(q * price))`. Entry is the **next source-calendar open** and exit is
the open **h sessions after that entry** (`legacy_next_open_plus_h`). Each label
is `(S_valuation - S_origin) / G_origin`. Current S may be zero or negative;
dividing by spread value would be a unit/direction error. Quantities never change
between current, entry and exit. A future F could predict normalized change and
reconstruct basket value as `V = S + G * g`; this package produces neither F nor V.

Each valuation has its own status and missing-leg list; each label also records
why it is unavailable. No available half-pair is valued. Missing source rows do
not compress the calendar. Tail labels outside the calendar remain present and
unavailable. Floating-point overflow/underflow cannot create a finite-looking
partial-leg value. `label_is_mature` uses a strict date cutoff, excluding labels
on the cutoff day. It expects a row returned by this kernel, not arbitrary input.

Every origin must follow the declared quantity cutoff. That structural check
does **not** prove that a human chose the pair or quantities using only the
training prefix. Historical provenance, episode-local pair formation, and
whole-process outer evaluation remain separate work. Fixed quantities are an
input assertion here, not a fitted result or an economic default.

The source descriptor binds an opaque owner key, exact dataset and scope refs,
full U, source range/calendar, and adjusted-share units. It prevents accidental
cross-source reuse inside this kernel. The projection root binds the actual
normalized local price rows. Neither self-consistent descriptor hashes nor a
provided datasetRoot authenticate a source: a future adapter must verify the
original manifest/scope/parts and their ownership before constructing this input.
No source file, original archive, service or database is read here. The supported
price basis matches the existing market source's first-observed adjustment;
mixing raw currency prices, adjusted prices or raw-share quantities is rejected.

The finite local shape bounds are parser/allocation limits, not measured capacity
or admission to the 1000-asset mean/trend profiles. Over-limit U/T is rejected
whole, never reduced. Pair capacity needs its own future declaration and measured
acceptance including diagnostics, shared-leg dependence and baseline equality.
No existing registry, strategy schema, guard, claim, feature gate or UI is changed.

Tests use tiny hand-written prices only. Run from the repository root:

```sh
PYTHONPATH=engine .venv/bin/python -m pytest -q engine/tests/test_pair_research.py
```

The next stages remain: authenticated source projection, Studio integration,
episode/feature/F and baseline contracts, independent paired-source auditing,
then separate capacity acceptance. Easy relation formation is a separate later
stage; proposed graph projections/correlation thresholds/degree rules are not
implemented or approved defaults in this package.

## Stage 2A: local source projection and causal Samples

`source.py`, `research_contract.py`, `features.py`, and `samples.py` add a separate
local preparation path. The Stage 1 exports and schema above are unchanged.
Stage 2A does not fit a model, estimate quantities, preprocess training data,
select pairs, acquire data, call HTTP, register a profile, or authorize hosted
research. It is not Easy formation, a strategy, or the complete pair F loop.

`read_market_source(reader, expected_domain=...)` accepts an injected fixed
`MarketSourceReader` (including its bounded directory reader), verifies the
strict `market_dataset/1` raw archive with the existing normalization routine,
and checks its dataset root, scope reference, complete U/range/calendar and
adjusted-share basis against mandatory caller pins. It preserves observed rows
separately from the complete U x calendar feature grid; absent sessions become
null grid cells and are never forward filled or removed.

The caller must supply `expected_domain` explicitly. The adapter does not infer
trusted expected pins from its own archive. Even matching pins establish only
consistency. `ownerKey` and `marketDatasetRef.datasetId` are caller assertions
absent from the market manifest, explicitly listed as unverified. Source/owner
authority, historical membership/revision vintage, and original provider-wire
authenticity remain false. Frozen source bytes do not prove historical
availability. Local objects are detached immutable bytes/tuples, not serialized
authorization credentials or a security boundary against arbitrary Python code.
Neither legacy caches nor forecast snapshots are accepted through this adapter.
Hosted financial compose still accepts only its existing forecast snapshot
contract; this code neither bypasses nor extends it.

The entry points are explicit and require no fit:

```python
from atlas_quant.pair_research import declare_targets
from atlas_quant.pair_research.source import read_market_source
from atlas_quant.pair_research.research_contract import research_origins, declare_research
from atlas_quant.pair_research.samples import build_samples

source = read_market_source(reader, expected_domain=caller_pinned_domain)
origins = research_origins(source, quantity_cutoff=cutoff,
                           factors=predictors, observation_days=1)
targets = declare_targets(source.price_input, explicit_pair_map,
                          quantity_cutoff=cutoff, origins=origins,
                          horizon_sessions=2)
research = declare_research(source, targets, factors=predictors, observation_days=1)
prepared = build_samples(source, targets, research)
```

Each predictor has exactly `id`, `expression`, `direction` (integer +/-1), and
`role="predictor"`. Expressions reuse the existing causal DSL; only native
market fields actually present in this source are accepted. No hedge/event,
financial/external fields, arbitrary callables, model controls, unknown fields,
or execution flag are permitted. Explicit empty predictors and empty T remain
valid preparation results, never an instruction to start F.

The required origin grid starts at max(61, DSL lookback+1, cutoff index+1), then
steps by the explicit observation interval. It is computed without consulting
target/feature availability or future labels. Stage 1 origins must match
exactly; the adapter rejects instead of trimming an existing declaration.
Quantity cutoff precedes every potential training/test origin, but remains an
assertion about the supplied q, not proof that selection or formation avoided
future data. `legacy_next_open_plus_h` remains explicit: entry is next source
open and exit is h sessions after entry, with both labels normalized by origin
G. Zero/negative S is valid and is never used as the denominator.

For each origin, only the complete U prefix through that date reaches
`factors.evaluate_expression`. Unmatched members participate in cross-sectional
rank/zscore. The four existing pair-family state features are reused through
`statistical_quant.targets._features`, with a fixed-q 61-close history. Leg
predictors map to each pair as `sum(q_i * close_i / G * direction * factor_i)`;
their expression units are preserved, without claiming automated dimensional
analysis. Missing required close history invalidates the state input. A missing
factor leg leaves that factor null (no partial-leg substitution or imputation);
a future training-only transform may handle it under a separate F contract.

`PairSamplePreparation.samples` uses the existing `Samples` structure with
complete T x origins, exact two-column y, and explicit state/feature/entry/exit
statuses. It preserves mature, missing-leg, and out-of-calendar tail rows, along
with every unmatched U member in `targets` and `evidence`. `hedge_fits=[]` and
`modelAvailable=False` mean no quantity/model fit occurred. Source domain,
price projection, raw feature source, sampling contract, and constructed X have
separate bound roots; feature-prefix roots record actual <=origin content.
No external feature table can be injected into `build_samples`. Source/target
swaps and stale DSL/feature bindings reject before calculation.

Local rejection envelope: U<=50, predictors<=16, source range/calendar<=366 days,
observed source rows<=18,300, raw archive<=32 MiB, normalized collections<=8 MiB,
and T x origins<=110,000, in addition to Stage 1's pair/quantity bounds. These
are parser/allocation limits, not measured pair capacity. Oversized full pools
(including 1000 members) reject whole before reading parts; no implicit subset.
Stage 2B shared F/baseline scheduling, portable function metadata, independent
paired-source auditing, capacity, service admission and UI remain unimplemented.

Run the isolated no-fit tests alongside the unchanged Stage 1 suite:

```sh
PYTHONPATH=engine .venv/bin/python -m pytest -q \
  engine/tests/test_pair_research.py engine/tests/test_pair_research_samples.py
```
