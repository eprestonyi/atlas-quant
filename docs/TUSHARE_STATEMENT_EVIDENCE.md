# Tushare statement evidence and adapter boundary

Reviewed **2026-10-08** against public first-party documentation and this financial worktree. The initial documentation-only checkpoint made **zero provider data requests and read no credentials**. A later, separately authorized six-request canary is recorded at the end of this document. Neither checkpoint changed production; the canary's exact sample evidence must not become a universal unit or historical-coverage claim.

The numerical contracts are in [FINANCIAL_STATEMENTS_CORE.md](FINANCIAL_STATEMENTS_CORE.md); the broader design is in [FINANCIAL_FACTORS_PLAN.md](FINANCIAL_FACTORS_PLAN.md). A core fixture passing is not evidence about a vendor field's unit.

## Confirmed field identities; units remain unverified

The following 15 fields are listed as floating-point outputs, normally returned by default. The brief meanings below identify the mapping; they do not establish monetary scale or currency.

| Official endpoint | Field | Meaning | Unit / currency evidence |
| --- | --- | --- | --- |
| [income](https://tushare.pro/document/2?doc_id=33) | `revenue` | Operating revenue | UNKNOWN |
| income | `operate_profit` | Operating profit | UNKNOWN |
| income | `n_income` | Net profit including minority interests | UNKNOWN |
| income | `n_income_attr_p` | Net profit excluding minority interests | UNKNOWN |
| [balancesheet](https://tushare.pro/document/2?doc_id=36) | `total_assets` | Total assets | UNKNOWN |
| balancesheet | `total_liab` | Total liabilities | UNKNOWN |
| balancesheet | `total_cur_assets` | Current assets | UNKNOWN |
| balancesheet | `total_cur_liab` | Current liabilities | UNKNOWN |
| balancesheet | `money_cap` | Cash and monetary funds | UNKNOWN |
| balancesheet | `accounts_receiv` | Accounts receivable | UNKNOWN |
| balancesheet | `goodwill` | Goodwill | UNKNOWN |
| balancesheet | `st_borr` | Short-term borrowing | UNKNOWN |
| balancesheet | `lt_borr` | Long-term borrowing | UNKNOWN |
| [cashflow](https://tushare.pro/document/2?doc_id=44) | `n_cashflow_act` | Net operating cash flow | UNKNOWN |
| cashflow | `c_pay_acq_const_fiolta` | Cash paid to acquire/build long-lived assets | UNKNOWN; numeric sign convention also unverified |

None of these field descriptions explicitly establishes **CNY yuan**. Other fields on the balance-sheet/cash-flow pages have explicit yuan annotations; that does not establish the units of this selection. The payment description identifies an economic outflow, but does not specify a universally positive stored sign. No common currency output is listed on these three pages. Targeted searches within official Tushare documentation did not resolve these omissions; this is a bounded search result, not proof that no vendor contract exists.

**Adapter consequence:** the core's `canonical_unit="CNY"` and registered scale conversions are destination contracts, not provider evidence. Do not create `UnitEvidence(verified=True)` from these URLs alone, response magnitudes, A-share listing location, matching ratios or synthetic fixtures. Obtain a field-specific vendor contract/source statement with scale, currency and capex sign, or return `UNIT_UNVERIFIED` / the corresponding unsupported state.

## Dates, filters and disclosure versions

All three pages distinguish announcement date (`ann_date`), actual announcement date (`f_ann_date`) and report-period end (`end_date` in outputs). Their **input** `start_date/end_date` descriptions are announcement bounds; `period` selects a reporting period. Income and cashflow document `f_ann_date` as an input; balancesheet lists it only as output. Boundary inclusivity and the relationship between date-range filtering and actual announcement dates are unspecified. [Income parameters](https://tushare.pro/document/2?doc_id=33), [balance-sheet parameters](https://tushare.pro/document/2?doc_id=36), [cash-flow parameters](https://tushare.pro/document/2?doc_id=44).

The balancesheet example requests an end bound of `20180730` but displays `ann_date=20180830`. This conflicts with the written announcement-range definition. It is not evidence that the endpoint uses report-period filtering instead. Preserve the discrepancy for an explicit, bounded canary before relying on remote filtering. [Official example](https://tushare.pro/document/2?doc_id=36).

The existing core's later-of-two-announcements, strictly-next-session rule is **our conservative research policy**, not a Tushare guarantee. The pages do not establish intraday release time, timezone, complete revision arrival history or original-as-published availability. Retain the existing original-version-unverified label. `update_flag` must not become a publication timestamp: cashflow describes `1` as latest, while income/balancesheet give only a generic update label. [Cashflow output contract](https://tushare.pro/document/2?doc_id=44).

## Company and report classifications

The wire name is **`comp_type`**, not `company_type`: `1` industrial/commercial, `2` bank, `3` insurer, `4` securities, `7` diversified finance. The core's current restriction to `1` is recipe scope, not a provider limitation. [Official company classification](https://tushare.pro/document/2?doc_id=33).

Report types separate consolidated (`1`), single-quarter consolidated (`2/3`), adjusted/original consolidated (`4/5`), parent (`6`), parent single-quarter (`7/8`), and adjusted/original parent (`9/10/12`). Default `1` selects the latest consolidated report. Type `11` mixes parent and consolidated terminology; `10/12` descriptions overlap. Keep `11` unsupported and retain raw types. The labels alone do not prove that every non-single-quarter flow is cumulative YTD or that revisions are mutually restated. [Official report-type table](https://tushare.pro/document/2?doc_id=33).

`end_type` is listed without a usable enum contract. Cashflow additionally documents integer `is_calc`, but supplies no value/default or calculation methodology. Do not infer calendar/fiscal basis from `end_type`, or label omitted `is_calc` as proof that rows are uncalculated source disclosures. [Cashflow contract](https://tushare.pro/document/2?doc_id=44).

## Record limits and local implementation

The three statement pages describe ordinary single-security access and separate VIP quarterly bulk access; they do **not** establish a numeric response cap in the reviewed text. By contrast, [fina_indicator](https://tushare.pro/document/2?doc_id=79) explicitly states 100 records per request and describes its range parameters as **report-period** bounds. Neither that cap nor that date semantics can be transferred to the three statements.

Read-only code findings in this worktree:

| Location | Present behavior | Required interpretation / follow-up |
| --- | --- | --- |
| `engine/atlas_quant/connectors.py:48–70` | Fixed output projections include all 15 fields; three statement limits are each `1000`. | This is a **local rejection threshold**, not a verified Tushare cap. Fewer than 1000 rows does not prove completeness. |
| `engine/atlas_quant/connectors.py:73–101` | Whitelisted string parameters, parsed dates, paired range bounds and a required symbol plus date selector. | `report_type`/`comp_type` are not currently enum-validated. Narrow the future adapter explicitly; do not describe existing validation as semantic validation. |
| `engine/atlas_quant/connectors.py:59–67`; `edge/portal-entry.mjs:5` | Neither accepts `f_ann_date` input, `is_calc`, `limit` or `offset`. | Document the narrower adapter; do not silently pass unsupported documented options or assume pagination. |
| `engine/atlas_quant/provider.py:256–341` | Sends the whole registered projection, requires its columns, bounds attempts/bytes, rejects responses at the local row threshold. | This does not yet provide dependency-minimal statement requests, verified completeness or per-field financial semantics. |
| `engine/atlas_quant/connectors.py`, `load_financial_history` | Existing history loader targets `fina_indicator`. | Its report-period window logic must not be reused unchanged for statement announcement windows. |

At the initial review checkpoint the separate statement adapter was design-only. Its subsequently implemented fake-provider tests and exact unit-binding contract are documented in [the core specification](FINANCIAL_STATEMENTS_CORE.md). The canary below tests fixed-period source observations; it does not validate announcement-window filtering or complete histories. No existing alias, stored forecast or production runtime was changed by this review.

## Follow-up: general official documentation

A second focused review on 2026-10-08 included the official FAQ, update directory, change log and SDK upgrade page, rather than only the endpoint field tables:

| First-party source | Additional evidence | What it does not establish |
| --- | --- | --- |
| [FAQ, question 7](https://tushare.pro/document/1?doc_id=122) | Financial duplicates commonly reflect corrections; `update_flag=0` denotes initial data and `1` corrected data. The flag can be explicitly requested. | No unit/currency rule, correction timestamp, complete revision chain or guarantee that every initially published record remains queryable. |
| [Data update / permission directory](https://tushare.pro/document/1?doc_id=108) | Lists the three statement endpoints as historical data with ongoing updates. | A catalog coverage description does not prove original-version availability, row completeness or a monetary-unit contract. |
| [Data additions and changes](https://tushare.pro/document/1?doc_id=9) | The currently visible change log covers November 2024 through September 2026. | No three-statement unit or currency standard was found in those entries. A generic VIP capacity announcement is not an ordinary statement endpoint limit. |
| [SDK upgrades](https://tushare.pro/document/1?doc_id=8) | Documents upgrading the client package. | Does not define statement units or alter the endpoint semantic contract. |

The FAQ refines the earlier endpoint-only observation about `update_flag`; retain both evidence levels. `report_type=1` being consolidated is established by the endpoint report-type tables above. It does not establish monetary units. No first-party common-unit FAQ or change notice located in this bounded search resolves the selected 15 fields as CNY yuan. The result remains **UNVERIFIED**, not an assertion that the vendor can never supply such a contract.

## Predeclared narrow cross-check: two fixed annual reports

The following protocol and acquisition checkpoint were fixed **before provider verification**. They are retained as the original plan; the later execution record follows separately. These two manufacturing-company candidates and one period were predeclared before reading provider values:

| Candidate | Fixed period / document | Official discovery route | Current acquisition boundary |
| --- | --- | --- | --- |
| Haier Smart Home, `600690.SH` | `20241231`, Chinese accounting-standard 2024 annual report, consolidated statements | [Issuer investor-relations index](https://smart-home.haier.com/cn/gpxx/?id=share_history) | The index identifies the annual report; its previously indexed PDF currently resolves to an error page in this review tool. No immutable PDF captured or cells verified here. |
| Gree Electric Appliances, `000651.SZ` | `20241231`, Chinese 2024 annual report, consolidated statements | [Issuer investor-relations page](https://gree.com/zh/zh-CN/investor/) | The old issuer PDF URL returned 404. Retrieve the exact report through the issuer's current reports page or its exchange disclosure, then verify the document identity. No cells verified here. |

Keep the predeclared samples if acquisition fails; record the gap rather than replacing them with easier values or substituting a later report's comparative column. First obtain each original issuer/exchange filing and its announcement record. Freeze original bytes and SHA-256, retrieval timestamp, issuer/security, publication date, accounting standard, report period and audited consolidated scope. A board-approval date, web-upload date or search-result date is not automatically the exchange disclosure date.

For each of the 15 fields, record the printed page **and PDF page index**, statement heading, exact row and year-column coordinates, raw numeric text, table-specific unit/currency declaration, footnote qualifications and reviewer. Use the consolidated statement, not the parent-company table, summary slide, management discussion or financial-institution subsidiary accounts. `revenue` must map to its revenue row rather than an unexamined total-revenue aggregate; the two net-profit fields must preserve minority-interest scope. Blanks and dashes remain missing unless that report explicitly defines them as zero.

For capex, inspect the acquisition/construction payment row within investing cash **outflows**, its positive or negative printed value, the outflow subtotal and the investing cash-flow reconciliation. This can establish the source filing's sign for that exact cell. It cannot, by itself, establish how Tushare encodes the corresponding field. Do not infer a vendor sign from the English/Chinese field name alone.

Only after both source dossiers are ready should a separately controlled provider canary be executed. The proposed initial budget is **six read requests total**: two securities × three endpoints, using `period="20241231"`, `report_type="1"`, `comp_type="1"` and the exact security; no automatic alternate periods, symbols, VIP calls or unbudgeted retries. This avoids relying on the contradictory announcement-window example. Reuse a suitable existing immutable response instead of fetching it again. This review made none of those requests.

Freeze the request projection and response bytes. Require the returned symbol, period, company/report types and disclosure version to match the source dossier; retain `ann_date`, `f_ann_date` and `update_flag`. If the default/latest consolidated response represents a later restatement, mark that version mismatch instead of comparing it to an earlier original filing. Any additional revision-specific query needs a new explicit budget; the initial six calls are not a completeness test.

Compare monetary values using Decimal and table-declared scale, retaining raw values. Predeclare candidate vendor scales `1`, `1000`, `10000`; report which, if any, uniquely reconciles each nonzero cell. Do not choose an arbitrary scale or relative tolerance to force agreement. The rounding bound comes from the displayed source precision and any explicit vendor precision contract; absent the latter, preserve the discrepancy and require review. Zero/null cells cannot identify the vendor scale. Two coincident ratios do not establish the constituent fields' units.

Each mapping result must be scoped to **endpoint + security + report period + report/company type + disclosure identity + field + PDF hash + provider response/record hash**, with source scale/currency, candidate vendor scale, sign evidence, precision policy and status (`matched`, `missing`, `version_mismatch`, `unit_unresolved` or `value_mismatch`). If all relevant evidence matches, a reviewed `source_document` unit reference may cover only those exact immutable observations. Loading another security, period, version or provider snapshot must not inherit that verification. The adapter must enforce that scope before constructing the core's `UnitEvidence`; its boolean alone is insufficient.

Deliver two source dossiers and a **30-cell** comparison matrix, including failures. At this pre-canary checkpoint completion was **0 provider requests and 0/30 reconciled cells**. Even a later 30/30 match would establish neither a universal Tushare unit contract nor quarter/YTD semantics, revision completeness or a sufficient multi-period history for all 16 derived states. Additional dependencies remain independently unverified.

## Executed canary: exact 2024 observations only

The authorized canary completed on **2026-10-08 Asia/Hong_Kong**. Both original filings, publication-date evidence, field/page coordinates and expected Decimal values were frozen and hashed before the first provider request. Two securities × three endpoints made **six actual requests, no retries and no unknown outcomes**. Each request used its exact security plus `period=20241231`, `report_type=1` and `comp_type=1`; no alternate year, security, VIP endpoint or announcement-window query was used.

| Security | Official immutable filing | SHA-256 | Consolidated statement pages | Publication-date evidence |
| --- | --- | --- | --- | --- |
| `600690.SH` | [Haier 2024 annual report](https://static.cninfo.com.cn/finalpage/2025-03-28/1222926246.PDF), 247 pages, 5,167,345 bytes | `cb2830d04860a0c18a491b9998c0928d1b77e740f07824a34f7543d823f7b10b` | Balance 117–119; income 121–122; cash flow 124–125 | [Issuer's later announcement](https://static.cninfo.com.cn/finalpage/2025-05-29/1223714946.PDF), page 1, explicitly identifies release on 2025-03-28. |
| `000651.SZ` | [Gree 2024 annual report](https://static.cninfo.com.cn/finalpage/2025-04-28/1223330631.PDF), 248 pages, 5,087,621 bytes; byte-identical to the retained SZSE original | `c7184706caf5f57c04a795990967cfc4f4253024e02f8bdaffe2b260df2c6006` | Balance 111–112; income 113; cash flow 114 | [Issuer's later announcement](https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-06-03/e3829464-155d-4984-9b07-2fc7f86d5b33.PDF), page 1, explicitly identifies release on 2025-04-28. |

Printed and one-based PDF page numbers coincide in these two files. Full relevant pages were rendered and visually checked against text extraction, including statement boundaries and the table-specific CNY/yuan headings. Haier's IR listing/audit date of March 27 and the Gree exchange URL's April 25 storage date were retained as different facts; neither replaced the explicit issuer publication evidence. Intraday release times remain unknown.

All **30 unique security/field cells** reconciled exactly to the fixed annual-report cells with Decimal arithmetic, **scale 1 and zero tolerance**. The candidate scales 1, 1,000 and 10,000 were fixed in advance; each nonzero cell matched only scale 1. Both capex cash-payment cells were positive outflows, and each source table's outflow-component sum and investing-cash-flow reconciliation passed. These are per-cell source-document findings, not a universal vendor sign rule.

The six responses contained **seven rows**: Haier cash flow returned two records with update flags 0 and 1. Both records were retained; both selected cash-flow fields matched, giving **32/32 returned-row/field comparisons**. The returned symbol, report period, company/report types and both announcement dates matched the fixed dossier. No preferred revision was silently selected, and matching selected cells does not certify every unexamined field or a complete revision history.

Private evidence includes exact raw response bytes/SHA-256, durable pre-request intents and outcomes, the frozen expectations, source-page coordinates, the 30-cell matrix and all 32 row comparisons. Offline reconstruction also retained the distinct `normalized_provider_table_snapshot` and `normalized_provider_table_row` identities under the current client JSON/pandas normalization. Original numeric lexemes were used for the source comparison; all selected normalized values separately retained the source's displayed decimal value. The normalized hashes are **not** wire-response hashes.

The 32 `DocumentUnitBinding` records cover only their exact provider, field, security, report period/type, company type, announcement dates, normalized snapshot/row hashes and original PDF hash. All 32 bindings matched offline, while **288 coordinate/provider/snapshot perturbations** failed closed. The actual period-selector request and retrieval time are part of each snapshot; no announcement-window request was fabricated. The bindings remain private evidence and are not installed as a global contract or in production.

Private dossier: `private/statement-canary-20261008/summary.json`, with `comparison-matrix.json`, `normalized-adapter-snapshots.json` and `document-unit-bindings.json`. Raw provider responses and monetary observations are excluded from the public repository. This checkpoint does **not** establish original-as-published vendor history, full correction arrival times, announcement-window filtering, completeness, quarterly/YTD semantics, an official trading calendar, a daily PIT panel or all 16 multi-period derived states. No research strategy or live service was changed.
