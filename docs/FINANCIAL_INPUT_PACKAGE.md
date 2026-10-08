# Frozen financial input and offline preparation

This financial-worktree slice prepares the sixteen native statement states from frozen inputs. It does not register them in the hosted catalog, connect them to F, add a browser/API upload route, fetch data, or deploy a service. See [the statement core](FINANCIAL_STATEMENTS_CORE.md) for formulas and [the source review](TUSHARE_STATEMENT_EVIDENCE.md) for the narrow real-data evidence.

## Single computation path

`prepare_statement_states` accepts frozen normalized table snapshots, a minimal universe (`symbols`, `start`, `end`), selected state IDs, a `TradingCalendar`, scoped unit bindings and an announcement-history bound. It has no network client, file reader or credential parameter. It validates snapshot hashes, request selectors, actual row identities and columns, then applies the same statement store and formulas used by `load_statement_states`.

`load_statement_states` now only acquires bounded snapshots and preserves its request journal before invoking that pure function. Replaying a package never synthesizes announcement-window requests: the original snapshot's actual period/announcement/range selector remains intact. Request journals do not affect the prepared numerical identity; original request parameters, retrieval times and source identities do affect the raw input identity.

The result contains the existing finite numeric panel with `__available_date` columns, Decimal state events, interval assignments, coverage and provenance. Each selected external field now also carries its actual formula definition/version, unit policy, evidence levels, declaration hashes and quality flags. `preparedRoot` hashes the raw input root, unit policy, selection, panel, complete events, assignments and coverage. `packRoot` additionally commits to all supplied bindings, including unused proofs. Preparation returns missing states and reason counts when dependencies are absent; it does not silently omit an unavailable selected state.

## Two roots, no circular declaration

The JSON package is `format="atlas.quant.financial-input"`, `version=1`:

```text
raw = snapshots + TradingCalendar + sourceKind + sourceProvider
inputRoot = SHA256(canonical JSON(raw))

DeclaredUnitBinding.input_root = inputRoot

package = format + version + inputRoot + raw + selection + unitPolicy + bindings
packRoot = SHA256(canonical JSON(package without packRoot))
```

Declarations and policy are deliberately excluded from `inputRoot`; they are included in `packRoot`. A different calendar, retrieval timestamp, request, row, field projection or source changes the raw root, so a declaration cannot transfer to it. A changed declaration or policy changes the package and prepared evidence identity even if numerical values happen to be identical. Roots provide integrity, not provider signatures or document authentication.

`freeze_package` produces a detached canonical JSON representation. `validate_package` revalidates the complete contents and roots; `decode_package` additionally rejects duplicate JSON keys and checks byte length before parsing. `prepare_package` revalidates before computing. No pickle, executable serialized object, arbitrary class name, filesystem path or remote fetch instruction is supported. A caller can mutate a returned Python dict, but then its old root will fail verification.

## Explicit research assumptions

`unit_policy="verified_only"` is the unchanged default. A `DeclaredUnitBinding` uses `UnitEvidence(kind="user_declared_assumption", verified=False)` and includes field ID, provider, exact raw input root, declaration author, timezone-qualified timestamp and a bounded explicit statement. `verified=True` is rejected for this evidence kind.

`unit_policy="allow_declared"` permits a matching declaration to supply a supported unit/currency and explicit capex sign for calculation. It does **not** verify the declaration. Every affected raw and derived dependency retains the exact scope and declaration hashes; every result carries `qualityFlags=["USER_DECLARED_UNIT_ASSUMPTION"]`, `unitEvidenceLevels`, `declarationHashes` and `unitVerified=False`. A missing result also retains these labels when the relevant dependency is known. Decimal arithmetic and missing-dependency rules are shared with strict mode.

Unknown units, unsupported currencies, missing/negative capex sign, conflicting matching declarations, wrong input roots, missing dates, unsupported report/company semantics, incomplete calendars, future disclosures and insufficient historical periods remain blocked or missing. The option only changes the admissibility of an explicit unit declaration; it is not a general ignore-validation switch. Declaration timestamps identify the research assumption, not the historical disclosure clock.

Example using already frozen inputs, with no provider read:

```python
from atlas_quant.financial_statements import (
    FIELDS, UnitEvidence, DeclaredUnitBinding,
    raw_input, freeze_package, prepare_package,
)
from atlas_quant.financial_statements.prepare import required_fields

_, input_root = raw_input(snapshots, calendar,
    source_kind="provider", source_provider="TUSHARE_PRO")
units = {
    field: DeclaredUnitBinding(
        evidence=UnitEvidence(
            native_unit="CNY", currency="CNY", verified=False,
            kind="user_declared_assumption",
            reference="Researcher's recorded unit interpretation",
            positive_outflow=True if FIELDS[field].positive_outflow else None,
        ),
        field_id=field, provider="TUSHARE_PRO", input_root=input_root,
        declared_by="workspace-researcher", declared_at="2026-10-08T00:00:00Z",
        statement="For this exact frozen input I interpret these amounts as CNY yuan; capex payments are positive outflows.",
    )
    for field in required_fields(selected_ids)
}
package = freeze_package(snapshots, calendar, strategy, selected_ids, units,
    announcement_start=history_start, unit_policy="allow_declared")
prepared = prepare_package(package)
```

## Trust boundary for reviewed proofs and calendars

Package import defaults to `trusted_unit_proofs=False`. It accepts unverified declarations but rejects caller-supplied fixture, document and global-contract bindings. A future public API must not take a client-controlled `trusted_unit_proofs` flag. It may accept declarations, or resolve references to proofs already reviewed through an operator-controlled process.

The explicit library option `trusted_unit_proofs=True` is an **out-of-band trusted-caller contract**, not an authentication procedure. Direct core and acquisition-adapter calls similarly consume caller-supplied typed proofs. This library verifies identity/scope consistency; it does not inspect a PDF, verify a signature, authenticate the reviewer or decide that an uploaded document proves its asserted units. A user-entered SHA is not platform verification.

Calendar authenticity is also the integration layer's responsibility. A future public API must resolve an authorized calendar source/root, not accept an uploaded `kind="official"` string as independent proof. The core still requires complete declared calendar coverage and uses the strictly next covered session after the later disclosure date. A source-document unit match cannot certify original-as-published history or intraday release timing. Existing `originalAsPublishedVerified=False` and `revisionTimeVerified=False` labels remain.

## Resource profile

- A frozen package is at most 24 MiB, with at most 128 normalized snapshots and 20,000 unit bindings.
- A calendar contains at most 10,000 sessions and at most 1 MiB of JSON. Raw tables retain the existing 20,000-row / 32-MiB preparation limits; the smaller complete-package cap applies when importing a package.
- The daily panel is at most 110,000 rows / 32 MiB; state events are at most 10,000 / 32 MiB. Raw input/calendar, bindings, expanded source-record evidence, panel, events, assignments and final provenance/envelope share a 64-MiB preparation ceiling. Raw input is checked before proof compilation. Each immutable binding is hashed once and indexed by exact matching scope; row lookup does not scan every candidate. Each source row's expanded evidence is charged before constructing its record, even if the store later deduplicates identical rows. Audit dependency expansion is checked before `to_dict`, and aggregate provenance has its own preflight within the same parent ceiling.
- Preflight uses conservative serialized-size bounds (including proof hash arrays and repeated references), so it may reject before the exact final JSON alone reaches the ceiling. These bounds do not claim an RSS limit for Python objects. Provenance records `bindingBytes`, `expandedRecordByteBound` and `resourceAccounting="conservative_serialized_expansion_v1_not_rss"`. Limits fail without publishing a truncated panel.
- These are local financial-preparation bounds, not permission to exceed a host application's overall input/result quota. The future runner/API must charge packages to its common parent budget and keep source/audit bodies out of small report manifests.

## Acceptance and real-data boundary

The new tests prepare all sixteen states through the frozen-package path from independently hand-calculated synthetic multi-period tables. They check equality with the acquisition adapter, default rejection and explicit admission of declarations, immutable roots, proof-import trust, changed calendars/scopes, conflicts, unknown units/currencies/signs, unavailable future disclosures and resource failures. Fake provider calls in the older adapter tests are in-memory fixtures, not network requests.

The retained real canary covers only `600690.SH` and `000651.SZ`, report period `20241231`: 30 unique cells / 32 returned-row comparisons. Those values supply the field dependencies for six static balance-sheet ratios: cash/assets, current coverage, liabilities/assets, borrowings/assets, receivables/assets and goodwill/assets. A single annual period does not supply the quarter/TTM/prior-year dependencies of the other ten states.

No authenticated raw `trade_cal` snapshot was located for the intended real-data interval in the bounded existing-artifact check. An older genuine research file has a derived session list and a provider-source string, but no raw calendar response/request hash; it was not upgraded to independent calendar evidence. The real package's daily preparation therefore remains **`CALENDAR_EVIDENCE_REQUIRED`**, with no substituted weekday calendar and no additional provider call. Six structurally supported formulas are not six globally available daily factors. Hosted catalog, F integration, UI/API admission and real daily-panel acceptance remain separate work.
