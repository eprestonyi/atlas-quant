# Atlas Quant v0.1 contract

This document describes the v0.1 interface between the browser, edge API, data provider and research engine.

Public URL /quant/. Relative API root /quant/api. All UI Chinese (English technical names okay), premium monochrome Atlas branding (#050505, #F2F0E9, #8B8B84, #9B9482); chart series semantic colors allowed. No fake real market data/results. Explicit synthetic tutorial mode.

## Strategy JSON

```
{
 "schemaVersion":1,"name":"我的因子策略",
 "universe":{"symbols":["000001.SZ","000002.SZ","600000.SH","600036.SH","600519.SH"],"start":"20230101","end":"20260930"},
 "factors":[{"id":"momentum_20","expression":"returns(close,20)","direction":1},{"id":"volatility_20","expression":"ts_std(returns(close,1),20)","direction":-1}],
 "preprocess":{"winsorize":true,"standardize":true},
 "model":{"mode":"auto","candidates":["factor_score","ridge","elastic_net","hist_gradient_boosting"],"horizon":5,"metric":"rank_ic"},
 "portfolio":{"topN":3,"maxWeight":0.4,"rebalanceDays":5,"initialCapital":1000000},
 "costs":{"commissionBps":3,"slippageBps":10,"sellTaxBps":5},
 "graph":{"nodes":[],"edges":[]}
}
```

Six typed sequential stages: universe -> factors -> preprocess -> model -> portfolio -> backtest. Frontend canvas can drag/reposition modules, add factors, configure each stage, connect valid typed nodes, reject invalid graphs. UI serializes graph and strategy above. Engine validates actual config independently. factor expressions are restricted causal DSL; never execute arbitrary code. max 20 symbols, max 12 factors, daily A shares, long only, bounded data and candidate budget.

## Engine contract

`run_research(strategy:dict, data:pandas.DataFrame, provenance:dict) -> dict` in atlas_quant.engine. Data rows columns ts_code, trade_date (YYYYMMDD string), open/high/low/close/raw_close/vol/amount/adj_factor; provider supplies adjusted OHLC in open/high/low/close, raw_close original. Optional daily_basic fields. Engine must retain causal shifts, use next-session open execution; panel folds split UNIQUE DATES, purge forward labels at fold boundary, final 20% holdout excluded from model selection, preprocessing fit on train only. Candidate baseline, Ridge, ElasticNet, HGB finite grid with early_stopping=False. Independent outer folds are implemented; the report labels evidence explicitly. No assumption of a profitable/best universal model. Execution in adjusted normalized research units (not brokerage shares), cost and trades reconcile, cash + positions marked daily. Explicit limitations: hypothetical fills, selected-today universe, corporate-action-adjusted units, not a full A-share exchange simulator. Missing sessions cannot fill.

Return JSON `schemaVersion, status, engineVersion, strategy, provenance, selection:{winner,metric,reason,candidates:[{id,name,score,folds,params,status}],splits,holdoutUsedForSelection:false}, metrics:{totalReturn,annualReturn,volatility,sharpe,maxDrawdown,turnover,totalCosts,tradeCount,benchmarkReturn}, equity:[{date,equity,benchmark,drawdown}], trades:[{date,symbol,side,quantity,price,notional,commission,slippage,tax,cost,cashAfter}], factors:[{id,ic,coverage}], warnings:[], validation:{...}`. All nonfinite values -> null. Fail insufficient data loudly. Include enough state for an audit. Engine owns built-in factors in engine/atlas_quant/catalog.json as `{factors:[{id,name,category,description,expression,direction,lookback}],models:[...]}`.

## Provider/runtime

`load_tushare(strategy, token, cache_dir=None)` returns (DataFrame, provenance), HTTPS official API only, strict parameters and limits, trade_cal plus daily+adj_factor, daily_basic optional only when fields required; permissions/rate errors explicit and no synthetic fallback. Cache bounded per token fingerprint, no token logs/files in repo. Tushare token may be supplied locally via env TUSHARE_TOKEN. An optional fixed internal provider proxy URL and service authorization come only from private runtime configuration and are injected in the trusted runner, never returned by the queue. Public provider access remains disabled pending rights.

`make_demo_data(strategy)` returns (DataFrame, provenance) with deterministic SYNTHETIC educational market, never called after provider failure. Explicit user selects demo. Demo should have enough observations + symbols for split validation, respects requested dates.

Runner reads private config outside repo with api_base + runner_secret, polls POST /runner/claim with bearer service auth. Returns `{job:null}` or `{job:{id,workspaceId,leaseToken,strategy,dataSource,dataset}}`; dataSource demo|upload|tushare. Demo generation, upload list of OHLC records, or Tushare via local env/internal proxy. Post `/runner/complete` `{id,leaseToken,result}` or `{id,leaseToken,error:{code,message}}`. POST `/runner/heartbeat` status optional; configurable idle polling, handle SIGTERM, one process job at time with timeout. Service lives under Application Support/YiCapital/atlas-quant, outward polling, no inbound tunnel.

## Browser API

GET /catalog returns `{factors,models,templates,dataSources,limits}`. Catalog public, builtins and published community entries.
GET /session creates/reads HttpOnly same-origin browser workspace cookie; returns `{workspace:{id,name},runner:{online,lastSeen},capabilities:{tushareHosted:false,upload:true,demo:true},sourceUrl}`. Personal data scoped server-side. Never spoof user identity from request body.
GET /strategies -> `{items}`; POST /strategies `{strategy}` -> `{item:{id,strategy,version,createdAt,updatedAt}}`; PUT /strategies/:id `{strategy,version}` revision check; DELETE /strategies/:id. Local export/import available UI.
GET /runs -> `{items:[{id,name,status,dataSource,createdAt,summary,error}]}`; POST /runs `{strategy,dataSource:'demo'|'upload'|'tushare',dataset?:{rows:[],provenance:{}}}` -> `{job:{id,status}}`; GET /runs/:id -> `{job,result?}`. POST /runs/:id/cancel; GET /runs/:id/export returns JSON. Max 1 active per workspace, bounded daily submissions. Raw datasets private, never public factor payloads.
GET /factors -> `{items}` public builtin/community. POST /factors `{name,description,expression,direction,category,author,license,sourceUrl}` -> `{item}`. Validate DSL syntactically allowlist; public contributions labeled community unreviewed; owner can DELETE /factors/:id, new versions use POST with forkOf. Factor details callable GET /factors/:id. Community factor definitions share, strategy/job private. Engine fully validates expression before using.
GET /health -> state only, no secrets. JSON errors `{error:{code,message}}`, status appropriate.

All browser mutation endpoints require same-origin and JSON. Session cookie secret random ID hash in DB, HttpOnly Secure Path=/quant/ SameSite=Strict. No reliance on localStorage account assertions. There is no account recovery-key route in v0.1. API result consumers never HTML-interpolate untrusted strings. No arbitrary Python execution.

## Acceptance

Clean setup can rerun deterministic benchmark; engine cost ledger test and no-future-label leakage tests pass. Public app drag canvas + configure factors + auto model + demo run/report + save/reload + factor contribution/fork + dataset import + JSON export work end-to-end. Tushare real-data verification distinct from synthetic. Deployment and public readback evidence explicit. Existing Atlas/Chat preserved; fresh production before/after digest; isolated /quant route preferably. Data authorization unresolved public shared Tushare feed remains disabled, not silently assumed.
