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
