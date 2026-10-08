# Whole-pool conditional trend research: isolated candidate

`pooled_asset_1000_trend_auto_v1` registers `family=trend`, `estimator=auto`,
`target.kind=asset_price`, and disabled execution. It does not grant a strategy,
prove predictability, or change either previously admitted market profile.
The declared full 1000-symbol synthetic capacity and loopback HTTP path have
passed. Production remains closed; this evidence does not establish market edge.

The pooled model estimates normalized changes at two existing engine endpoints:
the next open and that open plus the declared horizon. For the latter output,
`V = P + scale * g_h(X)` and `e = P - V`. This preserves the existing
`legacy_next_open_plus_h` timing; it is not silently relabelled as observation
time plus exactly `h` sessions. A declaration of the trend mechanism does not
force a positive momentum coefficient or a nonzero forecast.

Inputs comprise five built-in states (volatility20 and trend1/5/20/60) and at
most sixteen declared factor expressions. Automatic selection stays within this
mechanism: eight predeclared configurations spanning no-change, historical
drift, Ridge, Elastic Net and histogram boosting. Selection uses the existing
purged, time-ordered inner folds; outer folds estimate development performance;
the terminal block uses declared sequential refits with matured labels. Training
transforms are fitted within each fit. The factor-free branch separately selects
and fits using the same sample mask and built-in trend inputs.

The complexity/one-standard-error preference is a heuristic with dependent folds,
not a confidence interval or proof against selection bias. Pooled fits weight
rows equally, while validation loss averages dates. Frozen membership does not
establish point-in-time historical index membership. No RL is introduced.

## Exact gates

Worker admission requires both `MARKET_RESEARCH_ENABLED=true` and
`MARKET_TREND_AUTO_ENABLED=true`. A fresh runner must explicitly advertise the
new profile and `atlas.quant.bundle/1`; the ordinary market capability does not
grant it. Runner config requires both `market_dataset_research_enabled: true`
and `market_trend_auto_research_enabled: true` plus the existing private shared
compute lock. The new flag defaults to false. Old mean-auto and Ridge validators,
capabilities and numeric limits retain their existing semantics.

The research consumes the exact previously committed `market_dataset/1` source,
complete member set and source range. It creates a new research identity; it
neither changes the acquisition profile nor requests a provider. The independent
paired auditor separately enforces the profile's mechanism, complete origin ×
member domain, unchanged source rows and forecast identity arithmetic.

`scripts/preview-market.mjs --enable-trend-auto` explicitly enables this candidate
only in that loopback preview. With `--resume`, the existing session strategy,
scope, D1 and R2 identity stay intact; new trend research must be submitted as a
separate experiment/job. Omit the flag to keep trend auto disabled, including on
resume. This switch does not modify a production configuration.

## Evidence and next acceptance

The small synthetic vertical test uses three securities, all 262 sessions, sixteen
factor expressions and all eight candidates. It checks 123 main forecasts and
123 baseline rows, 105 matured plus 18 tail rows per branch, all 21 feature
diagnostics and all 210 empirical joint tables. Exported functions retain the
trend mechanism and full scope and satisfy the V/e identity. This is not 1000
security capacity evidence or validated forecast edge.

The acceptance used one newly declared HTTP F job, reusing the saved full
synthetic source. No offline 1000 fit preceded it. Before submission, freeze the
new job purpose, exact source ID/root, strategy, code identity and output folder.
The expected domain is 41,000 main and 41,000 baseline rows, including 6,000 tail
rows per branch; 106 scheduled fits (182 maximum attempts) include the independent
baseline. Preserve 900 s wall, 300 s fit, 3 GiB RSS, 400 MiB cache and 500 MiB free
disk guards. Retain any failure intact; do not shrink scope, skip candidates or
raise limits. Verify both original archives and independent paired audit, full
statistics, function resolution and HTTP terminal readback before a separate
decision about exposing the Easy path.

The run at candidate `83a59b9` completed as
`51485737-c5d6-446e-9ed8-bc289e29354a`, bundle
`5de3bb3caba61e6c50190f078154f4ca24cc7780ef8281bba3ac2f48e5253437`.
It retained the complete declared domain and 106 fit attempts. Engine work took
23.946 seconds, HTTP consumer work 44.087 seconds, peak RSS was 995,819,520 bytes,
and cache use was 154,415,359 bytes. Original source/result archives passed
32,806,260 and 808,343 independent checks. An additional review checked the
native browser downloads, all 59 result chunks, 106 fit clocks and full domains.
No source acquisition or provider request occurred. The one new F allowance is
exhausted; subsequent UI checks must use the frozen result.

The initial run submission was rejected before queue insertion because the API
compared scope JSON member order. A fresh experiment/D1 read showed no new job.
A separate durable intent then submitted the same values in the saved member
order, producing the sole new job. The original intent and readbacks remain;
the original 409 response body was not separately archived. The API now validates
the strict scope schema and compares identity fields independent of member order.

The browser displayed all 21 inputs and 210 pair tables, downloaded both archives,
and resolved/evaluated the portable function. Its JSON agrees with Python for
P=100 and scale=100: V=100 and e=0. The selected model is the no-change baseline;
no forecast improvement was validated. The new Easy binding reads exact live
admission and can explicitly reuse an existing source. Its isolated tests and
independent review pass; native source-rebinding and derived-edit acceptance on
the latest UI still awaits access to the unlocked host desktop.
