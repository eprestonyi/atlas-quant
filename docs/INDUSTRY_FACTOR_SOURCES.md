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

The 450 named source adapters comprise 6 broad China indices, 414 published SW indices, and 30 US ETF proxies. They are independent inputs; they are not estimated by averaging the currently selected output stocks. Research output and execution remain limited to the existing A-share universe.

China indices use `index_daily` or `sw_daily`. Source rows join the declared China trading grid by exact date; missing dates remain missing. Historical membership is not inferred from current classification metadata.

US ETF proxies use [Tushare `us_daily_adj`](https://tushare.pro/document/2?doc_id=338). Tushare documents `close × adj_factor` as the adjusted price. Both raw fields remain archived, and projection recomputes the adjusted mark. Zero, negative, missing, or nonfinite adjusted prices cannot become valid price inputs. The provider documents a separate US-data permission; enabling an adapter does not create that permission or prove ETF coverage.

At a China daily origin, the eligible US record is the latest source trading date strictly before the China date. This excludes the same-date US session, whose regular close occurs on the following China civil day in either daylight-saving regime. The availability companion records that next civil date. Marks older than seven calendar days are missing. No missing historical price is invented.

Subsequent returns and volatility are calculated on the declared China research grid using those previously known adjusted marks. A repeated mark is a carried observation; these are not relabelled as native US-session returns. The model's output stock calendar remains the China calendar. Provider revision history and exact intraday publication timestamps are not independently verified; the source package keeps `historicalRevisionVerified: false`.

Foreign inputs require runner capability `named-market-history/2`. Old runners cannot acquire these jobs. New envelopes use `named_market_series_asof_broadcast_by_date` and `source_session_publication_before_cn_origin`; China-only source envelopes retain the previous contract unchanged.

## Factors and archives

Price fields receive the existing automatic return/trailing-volatility transform when automatic preprocessing is selected. Global market/industry inputs are standardized over the training time axis and broadcast, never demeaned across stocks into zero. ETF volume and amount have US-specific units. Aliases identify the original selected fields while the frozen function artifact records the effective transformation.

Each distinct named source costs one bounded historical request per acquisition, even when multiple selected recipes share it. The existing limit of 16 independent sources per acquisition remains enforced. The complete source grids, request parameters, raw adjustment factors, hashes and actual availability companions travel in the existing context archive. Python restoration, edge admission, and the independent standard-library auditor reconstruct the same as-of projection.

The compact generated catalog contains recipe definitions. Its size and counts are UI inventory, not observed historical coverage or validated strategies. New source readbacks must record concrete API, symbol, interval, received rows and outcome separately; a successful sample cannot establish coverage for every symbol in the registry.
