# Industry and foreign-market inputs

The registry separates a classification identity, a named time-series adapter, and observed data. Neither a taxonomy row nor a generated factor recipe proves that a provider returned a usable history, or that a model has predictive value.

## Published identities

- China: SW2021 has 31 first-level, 134 second-level and 346 third-level classifications. The official Tushare document supplies the complete hierarchy. Two frozen `index_classify` reads on 2026-10-09 UTC checked all second- and third-level identities and publication flags: 124 and 259 were published. With 31 first-level indices, 414 SW index adapters are registered. The latest response corrects 特钢Ⅲ to `850401.SI`; the older document's `850412.SI` is not enabled.
- US: the official MSCI GICS structure effective 2023-03-17, with definition enhancements through February 2025, supplies 11 sectors, 25 groups, 74 industries and 163 subindustries. Retired workbook rows are excluded. These 273 classification identities do not assert that an index price series has been licensed or connected.
- ETF alternatives: 30 State Street sector/industry ETF identities are linked to relevant GICS classifications. Every relation is an explicit research proxy, not an assertion that ETF holdings exactly equal a GICS universe or that ETF performance equals an industry index. An unmapped classification remains unmapped.

Official sources:

- [Tushare SW classification](https://tushare.pro/document/2?doc_id=181)
- [Tushare SW daily history](https://tushare.pro/document/2?doc_id=327)
- [MSCI GICS](https://www.msci.com/indexes/index-resources/gics)
- [State Street sector ETF directory](https://www.ssga.com/us/en/intermediary/capabilities/equities/sector-investing/select-sector-etfs)
- [State Street ETF listing](https://www.ssga.com/library-content/pdfs/etf/us/spdr-etf-listing.pdf)

`data/sw2021-current-classification.json` stores the two parsed identity responses with request parameters, observation times, and original-response hashes. The original wire responses are private evidence. `scripts/import-industry-sources.py` imports factual identities from frozen official sources and this snapshot; it performs no HTTP requests. The MSCI workbook itself, including hidden worksheet material, is not distributed.

## Independent histories and clocks

The 480 named source identities comprise 6 broad China indices, 414 published SW indices, 30 historical Tushare ETF adapters and 30 separate Yahoo ETF adapters. The same ETF ticker on different providers has a different API/alias identity. They are independent inputs; they are not estimated by averaging the currently selected output stocks. Research output and execution remain limited to the existing A-share universe.

China indices use `index_daily` or `sw_daily`. Source rows join the declared China trading grid by exact date; missing dates remain missing. Historical membership is not inferred from current classification metadata.

Historical US ETF source packages use [Tushare `us_daily_adj`](https://tushare.pro/document/2?doc_id=338). Tushare documents `close × adj_factor` as the adjusted price. Both raw fields remain archived, and projection recomputes the adjusted mark. Zero, negative, missing, or nonfinite adjusted prices cannot become valid price inputs. The provider documents a separate US-data permission; enabling an adapter does not create that permission or prove ETF coverage.

At a China daily origin, the eligible US record is the latest source trading date strictly before the China date. This excludes the same-date US session, whose regular close occurs on the following China civil day in either daylight-saving regime. The availability companion records that next civil date. Marks older than seven calendar days are missing. No missing historical price is invented.

Subsequent returns and volatility are calculated on the declared China research grid using those previously known adjusted marks. A repeated mark is a carried observation; these are not relabelled as native US-session returns. The model's output stock calendar remains the China calendar. Provider revision history and exact intraday publication timestamps are not independently verified; the source package keeps `historicalRevisionVerified: false`.

Tushare foreign inputs require runner capability `named-market-history/2`; Yahoo inputs additionally require `named-market-history/3`. Old runners cannot acquire these jobs. New envelopes use `named_market_series_asof_broadcast_by_date` and `source_session_publication_before_cn_origin`; China-only source envelopes retain the previous contract unchanged.

## Factors and archives

Price fields receive the existing automatic return/trailing-volatility transform when automatic preprocessing is selected. Global market/industry inputs are standardized over the training time axis and broadcast, never demeaned across stocks into zero. ETF volume and amount have US-specific units. Aliases identify the original selected fields while the frozen function artifact records the effective transformation.

Each distinct Tushare named source costs one bounded historical request per acquisition, even when multiple selected recipes share it. Yahoo uses one library history invocation per source; cookie/timezone bootstrap may require additional HTTP requests, which are counted separately. The existing limit of 16 independent sources per acquisition remains enforced. The complete source grids, request parameters, raw adjustment factors, hashes and actual availability companions travel in the existing context archive. Python restoration, edge admission, and the independent standard-library auditor reconstruct the same as-of projection.

The compact generated catalog contains recipe definitions. Its size and counts are UI inventory, not observed historical coverage or validated strategies. New source readbacks must record concrete API, symbol, interval, received rows and outcome separately; a successful sample cannot establish coverage for every symbol in the registry.

## Live source samples on 2026-10-09 UTC

After the Portal's isolated wrapper deployment was read back, three initial history requests and two distinct diagnostic history requests were sent once each, without any model fit or retry:

| API / symbol | Requested interval | Result |
| --- | --- | --- |
| `sw_daily / 801125.SI` 白酒 | 2024-09-02 through 2024-09-13 | HTTP 200, provider code 0, 10 records |
| `sw_daily / 850818.SI` 半导体设备 | 2024-09-02 through 2024-09-13 | HTTP 200, provider code 0, 10 records |
| `us_daily_adj / XSD` | 2024-09-03 through 2024-09-13 | HTTP 200, provider code 0, **zero records** |
| `us_daily_adj / XLK` | 2024-09-03 through 2024-09-13 | HTTP 200, provider code 0, **zero records** |
| `us_daily_adj / XSD` | 2026-09-14 through 2026-09-25 | HTTP 200, provider code 0, **zero records** |

The empty XSD and XLK responses prove neither a permission denial nor ETF support. All 30 **Tushare** US ETF adapters remain `adapter_supported_history_unverified` in the library; their identities and adapter implementation are available, but they are not advertised as researched or connected histories. The two nonempty China samples do not establish all-symbol/all-date coverage. `data/industry-history-probes.json` preserves these exact scopes, statuses and wire hashes; private evidence retains every raw response. Including the two classification reads, this cycle consumed seven provider requests and zero model fits.

The official `us_daily` and `us_daily_adj` documents describe stocks and bare ticker symbols, without explicitly documenting ETF coverage. Testing a second ETF and a recent interval did not produce a positive observation; no unsupported ticker suffix or undocumented endpoint was guessed.


## Yahoo ETF history contract

The optional `yfinance==1.7.0` adapter uses a separate API identity, `yfinance_history`, and aliases such as `ext_ctx_yf_xsd_close`. Existing `us_daily_adj` sources and aliases are unchanged; archived Tushare data is never reinterpreted as Yahoo data. No Yahoo request passes through the Tushare Portal.

A single `Ticker.history` call uses daily intervals, inclusive start and exclusive end (the adapter converts its inclusive end to the next day), `auto_adjust=False`, `back_adjust=False`, `repair=False`, `actions=True`, `keepna=True` and `rounding=False`. The adapter requires ETF/USD/America-New-York identity from that same history response. Yahoo `Close` and `Adj Close` remain distinct. The research close uses **Adj Close directly**, without dividing and multiplying through a rounded adjustment ratio. Yahoo's Close must not be labelled as pre-split as-traded price. Volume is retained in provider share units. **There is no Yahoo amount field and no fabricated close-times-volume turnover.** Dividends and split observations accompanying the adjusted close stay in the archive.

The Yahoo source archive retains the existing context collection and adds a required `providerDetails` object only to `yfinance_history` envelopes: fixed library version, UTC acquisition time, validated currency/timezone/instrument type, one library call and bounded HTTP receipts. The receipts contain host/path/status/byte count/hash, never query strings, cookies or crumbs. The report source summary remains API/request/fields/record hash/row count. Older Tushare envelope keys and hashes retain their original contract. The records are parsed provider data, not wire bytes; their hash establishes content consistency, not provider authority or point-in-time revision history.

The HTTP wrapper allows at most eight requests per library invocation with 15-second per-request timeouts, a 90-second admission deadline, and an 8 MiB response limit. Application-level retries and provider fallback are absent. A failed chart request is not repeated even if the library attempts an alternate cookie strategy. The existing previous-US-session/maximum-seven-day projection applies before the factor's returns and trailing-volatility transform.

Two independent Yahoo samples were acquired on 2026-10-09 UTC:

| Symbol | Requested and returned dates | Rows | Library calls | HTTP requests | Result |
| --- | --- | --- | --- | --- | --- |
| XSD | 2024-09-03 through 2024-09-13 | 9 | 1 | 4 | Original chart retained; offline recovery after a receipt-path normalization validation error |
| XLK | 2024-09-03 through 2024-09-13 | 9 | 1 | 4 | Adapter completed |

XSD was **not fetched again**. Its already saved chart was parsed offline with the pinned library's quote parser; the absence of corporate-action events in that response was checked. Its original failed envelope result remains preserved. Both complete source grids subsequently passed offline parsing, as-of projection, engine validation and independent standard-library audit: 18 source rows, 8 two-stock/date broadcast rows and 50 consistency checks, with zero additional provider requests or model fits. These two initial samples enabled XSD and XLK only. The separate expansion below checks the remaining identities. A short positive sample never establishes all-date coverage or predictive performance. The public `data/yfinance-history-probes.json` contains metadata and hashes only; downloaded values and original response bodies are private.

Sources: [yfinance documentation](https://ranaroussi.github.io/yfinance/), [fixed package](https://pypi.org/project/yfinance/1.7.0/), [history implementation](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py), [Yahoo adjusted-close definition](https://in.help.yahoo.com/kb/adjusted-close-sln28256.html).

## Free-source and distribution boundary

[yfinance's documentation](https://ranaroussi.github.io/yfinance/) states that it is unofficial and that Yahoo Finance data is intended for personal use. The adapter is for owner-private research inputs. Open-sourcing its code grants no right to redistribute Yahoo data: downloaded prices, response bodies, cookies and private archives are excluded from public source distributions. No public quote redistribution endpoint or public Yahoo cache is added. [Yahoo's terms](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) remain applicable. Yahoo's website [CSV download workflow](https://in.help.yahoo.com/kb/finance/download-historical-data-yahoo-finance-sln2311.html) has separate subscription requirements; it is not represented as a free CSV service.

Two documented alternatives were reviewed, not silently invoked. [Alpha Vantage's free allowance](https://www.alphavantage.co/support/) is 25 requests/day, while its [adjusted daily endpoint](https://www.alphavantage.co/documentation/) is labelled premium. [Twelve Data Basic](https://twelvedata.com/pricing) lists US equities/ETFs with eight credits/minute and 800/day for individual internal use; it requires a separate account/key and its own terms. Neither is treated as an already authorized, licensed or adjusted-history-compatible fallback. Stooq's public historical-download page presented a verification challenge, so its adjustment and redistribution contract was not established and no challenge was bypassed.


A separately authorized expansion subsequently invoked history once for each of the other 28 ETF identities over 2024-09-03 through 2024-10-15. **27 returned 31 daily rows each. XTH's chart request returned HTTP 404**; the adapter blocked the library's automatic chart retry and preserved the failure. It did not substitute another symbol, provider or synthetic data. XTH remains unverified and cannot be selected for acquisition. The original XSD/XLK requests were not repeated.

Across both Yahoo probe groups: 30 library invocations, 120 observed HTTP responses, 29 positive ETF samples and 855 returned source rows, with no model fitting. Each initial and expansion call used four HTTP requests, including bootstrap; this is not 120 historical-series requests. The 27 new frozen sources passed another 1,269 offline consistency checks across two bounded groups, without further acquisition. The generated catalog now enables those **29 explicitly checked Yahoo ETF sources** and keeps XTH plus all 30 older Tushare ETF identities unverified. This is source connectivity and limited-window coverage, not a statement that 29 strategies work or that every GICS subindustry has an exact ETF proxy.
