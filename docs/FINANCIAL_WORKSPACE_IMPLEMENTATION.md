# Financial workspace implementation and acceptance

This implementation exposes frozen financial inputs through four independent UI
pages: source validation, units and reporting basis, state definitions, and
prepared coverage/lineage. It does not acquire provider data or attach financial
states to model research. The deployment flag `FINANCIAL_WORKSPACE_ENABLED` is
off unless explicitly set to `true`; every response retains
`researchBinding:false`.

The normative HTTP and runner contracts are
[FINANCIAL_WORKSPACE_CONTRACT.md](FINANCIAL_WORKSPACE_CONTRACT.md) and
[FINANCIAL_RUNNER_PROTOCOL.md](FINANCIAL_RUNNER_PROTOCOL.md). Formula, unit and
selection metadata comes from the installed Python core, exported with
`scripts/export-financial-definitions.py`; it is not a second JavaScript formula
implementation.

## Responsibilities and storage

| Component                                      | Responsibility                                                                                      |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `edge/financial/inputs.mjs`, `registry.mjs`    | Owner-scoped upload, immutable revisions, exact server registry references                          |
| `jobs.mjs`, `runner-api.mjs`                   | Separate financial capability, durable claims, lease/deadline checks, cancellation                  |
| `publications.mjs`, `integrity.mjs`            | Bounded chunk publication, hashes, structural closure, atomic visible completion                    |
| `read-model.mjs`, `api.mjs`                    | Bounded coverage/event/dependency pages and private attachments                                     |
| `engine/atlas_quant/financial_runner/`         | Authorized registry resolution, actual core computation, subprocess and encrypted delivery recovery |
| `web/quant-workspace/financial/`               | Four page UI, retained edits, loading/error states, coverage and source evidence                    |
| `edge/migrations/0006_financial_workspace.sql` | Additive tables and indices; the research queue is not repurposed                                   |

D1 stores ownership, task state, immutable references and bounded structural
indices. Financial numbers, source packages and dependency records stay in R2.
Worker completion checks coordinate coverage, interval coverage, event
references, status/null-mask consistency and hashes. These checks do not claim to
recompute Decimal financial formulas in JavaScript.

There is no public registry administration route. A client cannot upload
`trusted_unit_proofs:true`, self-certify an official calendar, or turn a user
declaration into verified evidence. Revisions retain the original input and
create a new package identity. `parent`/`consolidated` and `quarter`/`ytd` are
preserved explicitly; incompatible source records remain missing.

Bounds include a 24 MiB source package, 64 MiB published result, 512 KiB chunks,
256 KiB pages, one active task per owner and a 256 MiB retained storage budget.
Validate/revise tasks have a fixed 180-second deadline and prepare tasks a
600-second deadline. Heartbeats renew a 120-second lease without extending that
deadline. No claim that the maximum admitted preparation has been benchmarked
is made here.

## Reproduce the isolated synthetic workflow

Use the locked dependencies and installation steps in the repository README.
These commands do not need provider credentials or private acceptance files:

```sh
PYTHONPATH=engine .venv/bin/python scripts/make-financial-preview-input.py
npm run build
node scripts/financial-preview.mjs
```

The generator writes three files under
`private/financial-http-synthetic-canary/` and refuses to overwrite existing
files. They contain an explicitly synthetic snapshot, a synthetic calendar and
an explicit unit revision template. The fixture deliberately includes weekday
dates that must never be represented as an exchange calendar. To retain an old
fixture, generate into another directory with `--output` and pass that same
directory through `FINANCIAL_PREVIEW_FIXTURE_DIR` when starting the preview.

The preview prints its URL and the **path** to a mode-0600 bootstrap file. Do not
print or publish that file: it contains the local runner secret and an HTTP audit
owner cookie. The preview injects only the synthetic calendar into its isolated
D1/R2 registry. Its storage lasts for that process; restarting it is not a
persistent production setup.

In another terminal, start the real financial service through its restricted
developer entrypoint:

```sh
PYTHONPATH=engine .venv/bin/python scripts/financial-preview-consumer.py
```

This entrypoint admits only the generated loopback HTTP bootstrap. It uses the
production client, claim protocol, spawned computation, encrypted spool and
publication logic. It neither weakens the production HTTPS config loader nor
adds provider configuration. Stop it with Ctrl-C. Retain its spool until the
previous claim is acknowledged or terminal; do not copy an old spool into a new
preview that has a different secret/storage instance.

Open `http://financial.localhost:8924/quant/#quant/studio/financial`. A separate
hostname avoids cross-port `localhost` session cookies being replaced by another
open development preview; this is a local browser setup concern, not a production
authentication change.

1. Upload `strict-unbound-package.json`, choose the synthetic calendar, and
   validate. The source values have no verified units.
2. Prepare the strict input. All six panel rows remain missing; this is a valid
   completed preparation, not a failed task or invented value.
3. On the units page explicitly declare CNY for `money_cap` and `total_assets`,
   describe the synthetic assumption, confirm complete replacement, and save the
   immutable revision. No unit is selected implicitly.
4. Prepare that revision. Five rows have the hand-checkable ratio `2 / 10 = 0.2`;
   the first row remains missing until the conservative disclosure availability
   date. Inspect coverage, events and ordered dependencies. `unitVerified` stays
   false and the assumption label survives the derived formula.

State definitions can be searched and selected with keyboard-accessible buttons.
Selecting definitions does not imply source coverage. Coverage uses
`firstObserved`/`lastObserved`; original disclosure availability is shown
separately as `firstAvailable`/`lastAvailable`. The two date ranges can differ.

The UI download actions return the canonical package and complete individual
events. For a private independent R2 audit, write a new UUID `requestId` and an
optional `publicationId` to the bootstrap's `adminExportRequestPath`. The local
preview writes its readback receipt to `adminExportResponsePath`, and actual R2
manifest/chunks to `private/financial-preview-exports/<requestId>/`. This is a
local file control mechanism, not an HTTP admin endpoint. Never publish those
exports when they contain user financial records.

## Verification recorded on 2026-10-08

Evidence levels are distinct:

| Level                        | Result                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Node suite                   | 139 tests passed in the full run; the subsequently added financial migration suite passed separately (3/3), including v0.6.0 additive migration and queue isolation                                                                                                                                                                                                                                              |
| Financial API suite          | 11 real Miniflare D1/R2 tests, including actual Python core publication, concurrent retries, cancellation/expiry, corrupted TCP download, owner boundaries and parent/quarter revision preservation                                                                                                                                                    |
| Independent transport review | 17 tests passed; self-consistently rehashed incomplete coverage, empty panel, bad event references, missing assignments and inconsistent availability were rejected                                                                                                                                                                                    |
| DOM                          | Financial and statistical workspace checks passed; these use API doubles and are not browser visual evidence                                                                                                                                                                                                                                           |
| Static/build                 | `npm run check`, Python definitions export parity, and build passed                                                                                                                                                                                                                                                                                    |
| Real local consumer          | Upload/validate → strict all-missing preparation → explicit unit revision → usable preparation completed through real HTTP and spawned production consumer, without provider calls or model fitting                                                                                                                                                    |
| Independent R2/HTTP readback | Strict 304 checks and declared 309 checks passed; 17 read-only GETs verified two packages, full coverage/event pages and four complete event attachments against independent core recomputation                                                                                                                                                        |
| Actual browser               | Four pages, immutable revision, state selection, preparation, dependency inspection and download exercised. A real 390-pixel viewport had document scroll width 375 pixels, visible dark-theme progress/current step and no page horizontal overflow. Mobile event download was 4,795 bytes with two dependencies, ratio 0.2 and `unitVerified:false`. |
| Production                   | Not deployed or enabled by this work                                                                                                                                                                                                                                                                                                                   |

The private browser evidence includes
`private/browser-acceptance/financial-390-fixed.png`; private HTTP/R2 evidence is
kept under `private/financial-http-20261008T014023Z/` and
`private/financial-http-readback-20261008T014701Z/`. These directories are not
required for clean-clone tests and are not published source artifacts.

The attachment path explicitly disables HTTP content transformation and uses
fixed-length streaming plus source hashes. A real TCP corruption regression was
necessary: ordinary JSON gzip had previously hidden a truncated stream. Missing
or corrupt chunks now fail the download rather than silently delivering an empty
successful file. Event downloads query chunk descriptors once and read each
intersecting chunk once, rather than making a database and R2 request per
dependency.

## Deliberate remaining boundaries

- This workflow prepares inspectable financial states. Research dataset assembly,
  model attachment and F execution remain closed in this UI/API.
- A prepared source is not a ready research dataset. Future research binding
  must use the unified immutable `atlas.quant.research_dataset/1` reference and
  its server-side readiness checks.
- Declared units remain assumptions. Exact document-scoped verification cannot
  be generalized to other companies, report periods or frozen rows.
- Vendor-reported disclosure dates do not establish original as-published
  revision history. No synthetic fixture or future-perturbation test upgrades
  that evidence claim.
- Source acquisition, public calendar/proof registration, retention cleanup and
  account migration are not added by this workspace implementation.
