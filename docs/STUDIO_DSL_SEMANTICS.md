# Studio deterministic DSL semantics

Candidate only: branch `feat/quant-studio-semantics`, based on
`b24c68592ca30b84c65bed5c16e59f3a98fcdacf`. No deployment, provider request or real AI request was made for this change.
The source candidate is committed separately for root to integrate.

## Execution and explanation contract

`engine/atlas_quant/dsl_contract.json` is versioned
`atlas-factor-semantics/v1`. It ships inside the independently runnable Python
package. Python uses its field/operator sets, bounds, rolling-window offsets
and division threshold. The existing arithmetic evaluator remains unchanged.
The edge validator was extracted to `edge/factor-language.mjs`; it now retains
its parsed tree. Explanations walk that same tree and use the shared operator
contract. There is no second explanatory parser or AI-authored source of facts.

Examples distinguish:

- `returns(close,20)`: `close[t] / close[t−20] − 1`, cumulative endpoint change.
- `lag(close,20)`: the value at `t−20`.
- `ts_rank(close,20)`: within one security's 20 grid cells including the current one.
- `rank(close)`: same-date cross-sectional percentile rank.

The grid is the engine's complete market-session grid. It does not compress
suspensions/missing cells or count calendar days. Rolling `n` cells and `n`
endpoint intervals differ by one in the required lookback. Current complete
OHLC/volume inputs imply after-close observation; execution timing is separate.
Future target horizon is separate from historical lookback. Missing values,
zero denominators and final close masks are explicit. Cross-sectional operand
validity precedes the evaluator's final current-close mask.

`validateExpression` and Python `validate_expression` retain their historical
metadata shapes. Both consume `syntax.version = ascii_tokens_semantic_tree/v1`
from the same JSON. ASCII decimal numbers, identifiers and operators are
allowed. ASCII spaces/tabs/CR/LF separate tokens; `close\n+1` is accepted by
both. Python receives space-separated tokens, never concatenated tokens.
Leading-zero integer spellings, hexadecimal/underscore literals, Unicode names
or whitespace, comments, line-continuation escapes and trailing commas are
rejected by both. Nested unary window literals such as `lag(close,--1)` remain
invalid.

Complexity counts field, number, unary, binary and call nodes once each. Root
depth is 0; maximum depth is 16 and maximum node count is 128. Function names,
operator tokens and parentheses add no semantic nodes. Every call argument,
including window/bound literals, does count. Sixteen nested unary operators
remain accepted. Parenthesis nesting has an independent shared limit of 128,
before Python's own parser limit could cause cross-language disagreement.

These admission rules intentionally unify previously inconsistent edge/Python
syntax. They do not promise that every spelling accepted by one old parser
continues to be accepted. Existing saved artifacts are untouched; a rerun with
an unsupported spelling fails explicitly. All 397 catalog recipes retain the
same fields/lookbacks, and supported formula arithmetic remains unchanged.

## API and product behavior

`POST /expressions/lint` and DSL `POST /code/review` add `deterministicFacts` and
an exact submitted-code SHA-256. Facts contain the contract version, parsed
operations, window inputs, required fields, lookback, timing and missing-data
rules. Invalid input returns an invalid fact state without invented operations.
They explicitly say no data was executed and no correctness/predictive value
was certified.

Studio displays a neutral “DSL 确定语义” panel separately from “AI 审阅意见”.
AI provider completion is not a green correctness badge. Provider text cannot
override deterministic fields. Even a deliberately wrong AI summary remains
an opinion while the operator facts remain accurate. Patches still require an
explicit apply action. Typing, keyboard indentation, delayed responses and
applied suggestions preserve code and mark old evidence stale. A failed lint
request shows an error rather than resurrecting an older success. A successful
current-expression rule check clears a warning for a previous expression;
a late response cannot clear newer matching evidence or replace edited code.

The editor is still progressive: the outer operator and timing are visible;
inner operations, precise hashes and contract boundaries are expandable. This
is static semantics, not proof of data coverage, point-in-time disclosure
history, adjusted-price policy, program success or alpha.

## Verification performed

Commands from this repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=engine .venv/bin/python -m pytest -q engine/tests/test_dsl_adversarial_review.py engine/tests/test_dsl_semantics.py engine/tests/test_factors.py
node --test tests/dsl-semantics.test.mjs tests/studio-v2.test.mjs tests/validation.test.mjs tests/worker-source.test.mjs
node web/tests/studio-semantics-dom.mjs
node web/tests/studio-evidence-adversarial-dom.mjs
node web/tests/statistical-dom.mjs
node web/tests/research-flow.mjs
node web/tests/research-dom.mjs
node web/tests/sharded-report-dom.mjs
npm run check
npm run build
git diff --check
```

- Python: **111 passed** (65 original plus 46 independent review cases), including hand-computable operator examples, missing
  cells, ties, grid positions, zero denominators and independently copying the
  engine package with its contract JSON.
- Node/API/build: **24 passed**; all 397 existing catalog recipes have matching
  Python/edge fields and lookbacks. Actual Miniflare routing is exercised; AI
  transport is an explicitly wrong test double, not a provider call.
- Six DOM suites passed, including real input/click/keyboard events, delayed
  requests, failures, explicit patch application and existing workflow/report
  regressions. Independent project-switch and language-switch delayed-response
  tests also passed. These are structure/behavior tests, not layout proof.
- A separate bounded comparison swapped only old/new factor implementations
  in isolated full engine copies. Same 3-symbol, 1-year **synthetic** data and
  8 factors produced 120 forecast rows and byte-identical complete reports.
  Artifact ID: `951f411b698abf3e878349834c16085c3e49f92a868e789712877af941f52991`.
  Private reproduction receipt: `private/studio-semantics/identity-result.json`.
- Local HTTP on port 8919 returned the real parser's 20-cell cumulative formula
  and `executionPerformed:false`. No AI binding or runner is attached.
- Candidate build after parser-boundary fixes: `0.5.0-20b0e063eb77`.

## Actual browser acceptance and evidence boundary

The frontend agent's CUA session returned no available browser while the Mac
was locked. No other browser automation was used to bypass that session.
Root's independent in-app browser session remained available and performed
actual desktop and **390 × 844** acceptance: deterministic cumulative formula,
editing invalidates prior facts, negative windows fail, manual rule checking,
no horizontal overflow. Root retained a full-page mobile screenshot and
`private/studio-semantics/browser-desktop.png`. This is root-reported actual
browser evidence for the pre-boundary-fix candidate, distinct from jsdom.

Root then reproduced the stale bottom warning after invalid lint → edited
valid nested expression → successful rule check. This was fixed and covered
with actual DOM input/click events. Root refreshed build `0.5.0-20b0e063eb77` and actually verified both
`lag(close,01)` rejection and successful `rank(returns(close,20))` rule checking
with the stale bottom warning removed. Screenshot:
`private/studio-semantics/browser-fixed-desktop.png`. The earlier actual
390 × 844 check covers the same unchanged responsive CSS; no extra mobile pass
is inferred from DOM tests. Real AI-provider behavior with the new context
remains untested and is a separate post-integration check, not evidence needed
for the deterministic parser facts.

Preview remains at `http://localhost:8919/quant/#quant/studio/code`. Localhost
avoids unrelated preview servers sharing 127.0.0.1 workspace cookies; production
authentication was not changed. Recheck `returns(close,-1)` → “校验表达式” →
`rank(returns(close,20))` → “规则检查”: both current facts and review must show the
new expression, and the bottom old-expression warning must disappear. Also
check leading-zero `lag(close,01)` is invalid and `close` followed by a newline
and `+1` is parsed consistently. No real AI call is required or authorized for
choosing a better answer.
