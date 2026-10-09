"""Versioned numerical research recipes and their data-availability contracts.

Recipe counts describe definitions, not independent alpha discoveries or data
coverage. A field inventory never supplies observations or authorizes access.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

EXTERNAL_FIELD_PATTERN = re.compile(r"(?:pcd|fd|ext|model)_[a-z0-9_]{1,60}\Z")
WINDOWS = (5, 10, 20, 40, 60, 120, 180, 252)
EXTERNAL_RECIPE_TEMPLATES = [
    {"id": "level", "name": "已知数值", "expression": "{field}", "lookback": 0},
    {"id": "cross_section_rank", "name": "当日横截面排名", "expression": "rank({field})", "lookback": 0},
    {"id": "cross_section_zscore", "name": "当日横截面标准化", "expression": "zscore({field})", "lookback": 0},
    {"id": "lag_1", "name": "再滞后一交易日", "expression": "lag({field},1)", "lookback": 1},
    {"id": "change_20", "name": "20交易日前后已知值差", "expression": "delta({field},20)", "lookback": 20},
    {"id": "change_60", "name": "60交易日前后已知值差", "expression": "delta({field},60)", "lookback": 60},
    {"id": "mean_20", "name": "20交易日已知值均值", "expression": "ts_mean({field},20)", "lookback": 19},
    {"id": "time_rank_60", "name": "60交易日历史百分位", "expression": "ts_rank({field},60)", "lookback": 59},
]
TARGETS = {
    "forward_return": {"id": "forward_return", "name": "未来开盘到开盘收益", "unit": "fractional_return", "definition": "adjusted_open[T+h+1] / adjusted_open[T+1] - 1"},
    "forward_excess_return": {"id": "forward_excess_return", "name": "相对观测股票池的未来收益", "unit": "fractional_excess_return", "definition": "forward_return minus same-date mean forward_return of observed selected symbols", "limitation": "观测股票池不是可交易指数；未来缺失标签会影响基准组成。"},
}
MODEL_REGISTRY = {
    "factor_score": {"name": "等权因子基线", "description": "方向处理后同日因子排名均值；仅排序，不输出收益预测。", "family": "ranking_baseline", "predictionKind": "ordinal_score", "grid": [{}]},
    "ridge": {"name": "Ridge 回归", "description": "L2 正则化线性回归。", "family": "linear", "predictionKind": "target_estimate", "grid": [{"alpha": 1.0}, {"alpha": 10.0}]},
    "elastic_net": {"name": "ElasticNet 回归", "description": "L1/L2 稀疏线性模型；未收敛候选作废。", "family": "linear", "predictionKind": "target_estimate", "grid": [{"alpha": 0.0001, "l1_ratio": 0.2}, {"alpha": 0.001, "l1_ratio": 0.5}]},
    "hist_gradient_boosting": {"name": "Histogram Gradient Boosting", "description": "80 轮浅树，禁用随机 early stopping。", "family": "boosting", "predictionKind": "target_estimate", "grid": [{"max_leaf_nodes": 7, "l2_regularization": 1.0}, {"max_leaf_nodes": 15, "l2_regularization": 5.0}]},
    "bayesian_ridge": {"name": "Bayesian Ridge", "description": "训练段估计噪声与权重精度的贝叶斯线性回归；不宣称校准概率。", "family": "linear", "predictionKind": "target_estimate", "grid": [{}]},
    "huber": {"name": "Huber 稳健回归", "description": "分段平方/绝对损失降低极端标签影响；有限正则化网格。", "family": "robust_linear", "predictionKind": "target_estimate", "grid": [{"epsilon": 1.35, "alpha": 0.01}, {"epsilon": 1.75, "alpha": 0.1}]},
    "random_forest": {"name": "Random Forest", "description": "64 棵树、深度和叶节点正则化；固定 seed 与单线程。", "family": "bagging", "predictionKind": "target_estimate", "grid": [{"max_depth": 5, "min_samples_leaf": 20}, {"max_depth": 9, "min_samples_leaf": 40}]},
    "extra_trees": {"name": "Extra Trees", "description": "64 棵随机阈值树，浅树与大叶节点限制复杂度。", "family": "randomized_ensemble", "predictionKind": "target_estimate", "grid": [{"max_depth": 5, "min_samples_leaf": 20}, {"max_depth": 9, "min_samples_leaf": 40}]},
}


def is_external_field(name):
    return isinstance(name, str) and EXTERNAL_FIELD_PATTERN.fullmatch(name) is not None


def field_registry():
    from .connectors import FINANCIAL_FIELDS
    from .context_sources import field_registry as context_fields
    fields = []
    daily = {"open": "复权开盘价", "high": "复权最高价", "low": "复权最低价", "close": "复权收盘价", "raw_close": "原始收盘价", "vol": "成交量", "amount": "成交额", "adj_factor": "复权因子"}
    basics = {"turnover_rate": "换手率", "turnover_rate_f": "自由流通股换手率", "volume_ratio": "量比", "pe": "市盈率", "pe_ttm": "滚动市盈率", "pb": "市净率", "ps": "市销率", "ps_ttm": "滚动市销率", "dv_ratio": "股息率", "dv_ttm": "滚动股息率", "total_share": "总股本", "float_share": "流通股本", "free_share": "自由流通股本", "total_mv": "总市值", "circ_mv": "流通市值"}
    for name, label in {**daily, **basics}.items():
        source = "daily_basic" if name in basics else "adj_factor" if name == "adj_factor" else "daily"
        unit = "hands" if name == "vol" else "CNY_thousands" if name == "amount" else "CNY_10000" if name.endswith("_mv") else "shares_10000" if name.endswith("_share") else "percent" if name in {"turnover_rate", "turnover_rate_f", "dv_ratio", "dv_ttm"} else "ratio" if name in basics or name == "adj_factor" else "adjusted_research_price" if name != "raw_close" else "CNY_per_share"
        fields.append({"id": name, "name": label, "dataType": "number", "numericEligible": True, "source": "TUSHARE_PRO", "dataset": source, "unit": unit, "availabilityStatus": "adapter_supported_requires_observations", "minimumLagSessions": 0, "availability": "after_daily_provider_publication_before_next_open", "revisionHistoryVerified": False, "syntheticSupported": name in daily})
    for name, (label, unit) in FINANCIAL_FIELDS.items():
        fields.append({"id": "fd_" + name, "name": label, "dataType": "number", "numericEligible": True, "source": "TUSHARE_FUNDAMENTAL", "dataset": "fina_indicator", "unit": unit, "availabilityStatus": "adapter_supported_requires_observations_and_available_date", "minimumLagSessions": 1, "availability": "first_trading_session_after_disclosure_date", "revisionHistoryVerified": False, "syntheticSupported": False})
    return fields + context_fields()


def build_catalog():
    # Imported here so the DSL can import the tiny alias validator above.
    from .factors import validate_expression
    fields = {item["id"]: item for item in field_registry()}
    factors, seen = [], set()

    def add(ident, name, category, expression, direction, description, family, window=None):
        key = (expression, direction)
        if key in seen:
            return
        meta = validate_expression(expression)
        seen.add(key)
        datasets = sorted({fields[f]["dataset"] for f in meta["fields"]})
        external = any(field.startswith('fd_') for field in meta["fields"])
        factors.append({"id": ident, "name": name, "category": category, "description": description, "expression": expression, "direction": direction, "lookback": meta["lookback"], "family": family, "window": window, "requiredFields": meta["fields"], "sourceDatasets": datasets, "dataRequirement": "financial_pit" if external else "daily_basic" if "daily_basic" in datasets else "ohlcv", "availability": "first_trading_session_after_disclosure_date" if external else "after_required_daily_fields_are_published", "minimumLagSessions": 1 if external else 0, "lagAppliedBy": "point_in_time_data_join" if external else "factor_expression", "recipeVersion": 1, "license": "Apache-2.0", "status": "definition_only_requires_data", "researchStatus": "UNVALIDATED_HYPOTHESIS", "pitRevisionHistoryVerified": False})
        if any(dataset in {'index_daily', 'sw_daily', 'us_daily_adj'} for dataset in datasets):
            factors[-1].update(dataRequirement='named_index_history', scope='global' if all(field.startswith('ext_ctx_') for field in factors[-1]['requiredFields']) else 'asset',
                               automaticPreprocessingRequired=True, database='MKT')
        if 'us_daily_adj' in datasets:
            factors[-1].update(historyStatus='adapter_supported_history_unverified',
                               historyAvailabilityReason='ETF 历史待验；当前 XSD 样本区间未返回记录。')

    # Easy users choose a familiar raw concept; the versioned automatic
    # preprocessing policy chooses its economic transform before fold fitting.
    for raw, label, category in [('total_mv','总市值','规模'),('circ_mv','流通市值','规模'),
                                 ('close','价格','价格'),('vol','成交量','流动性'),
                                 ('amount','成交额','流动性'),('pe_ttm','市盈率 TTM','价值'),
                                 ('pb','市净率','价值'),('ps_ttm','市销率 TTM','价值')]:
        add('raw_'+raw, label, category, raw, 1, '原始数值输入；自动模式按类型处理，处理定义和训练参数随函数保留。', 'raw_input')

    from .context_sources import REGISTRY as CONTEXT_REGISTRY
    for source in CONTEXT_REGISTRY['items']:
        key = source['ts_code'].lower().replace('.', '_')
        close, amount = 'ext_ctx_'+key+'_close', 'ext_ctx_'+key+'_amount'
        for suffix, label, expression in [
            ('price','价格',close),
            ('momentum20','20日动量',f'returns({close},20)'),
            ('momentum60','60日动量',f'returns({close},60)'),
            ('volatility20','20日波动',f'ts_std(returns({close},1),20)'),
            ('drawdown60','60日回撤',f'{close}/ts_max({close},60)-1'),
            ('amount20','20日相对成交额',f'{amount}/ts_mean({amount},20)-1')]:
            add('context_'+key+'_'+suffix, source['name']+' · '+label, source['category'], expression, 1,
                '来自指定指数自身历史；与研究股票池独立，不代表当时行业成员归属。', 'named_index_'+suffix)
        beta = f'(ts_mean(returns(close,1)*returns({close},1),60)-ts_mean(returns(close,1),60)*ts_mean(returns({close},1),60))/(ts_std(returns({close},1),60)*ts_std(returns({close},1),60))'
        for suffix, label, expression in [
            ('beta60', '个股敏感度', beta),
            ('exposure_shock', '个股暴露 × 指数变化', f'({beta})*returns({close},1)'),
            ('relative_momentum20', '个股相对动量', f'returns(close,20)-returns({close},20)')]:
            add('context_'+key+'_'+suffix, source['name']+' · '+label, source['category'], expression, 1,
                '个股与指定指数独立历史联合构造；同一研究模型接收不同个股的暴露输入。', 'named_index_'+suffix)
        if source['api'] == 'sw_daily':
            for field, label in [('pe','市盈率'),('pb','市净率'),('total_mv','总市值')]:
                add('context_'+key+'_'+field, source['name']+' · '+label, source['category'], 'ext_ctx_'+key+'_'+field, 1,
                    '指定申万行业指数的已发布指标；原单位及自动处理随函数保存。', 'named_industry_level')

    # Separate parameter windows are named recipes, not statistically
    # independent discoveries. Each formula has an explicit interpretation.
    families = [
        ("momentum", "动量", "动量", "returns(close,{n})", 1, "复权收盘累计收益。"),
        ("reversal", "反转", "反转", "returns(close,{n})", -1, "累计收益的反向排序假设。"),
        ("volatility", "日收益波动率", "风险", "ts_std(returns(close,1),{n})", -1, "日收益总体标准差，偏好较低历史波动。"),
        ("range", "平均振幅", "风险", "ts_mean((high-low)/close,{n})", -1, "高低价差相对收盘价的滚动平均。"),
        ("volume_surge", "相对成交量", "流动性", "vol/ts_mean(vol,{n})-1", 1, "今日成交量相对包含当日的均量。"),
        ("volume_variation", "成交量变异", "流动性", "ts_std(vol,{n})/ts_mean(vol,{n})", -1, "成交量标准差除以均量，偏好稳定成交。"),
        ("liquidity", "对数成交额", "流动性", "log(ts_mean(amount,{n}))", 1, "平均成交额的自然对数，成交额单位千元。"),
        ("illiquidity", "非流动性", "流动性", "ts_mean(abs(returns(close,1))/max(amount,1),{n})", -1, "绝对收益除以成交额的平均，分母下限1千元。"),
        ("trend_strength", "均线偏离", "趋势", "close/ts_mean(close,{n})-1", 1, "当前收盘相对移动均价。"),
        ("range_position", "区间位置", "趋势", "(close-ts_min(low,{n}))/(ts_max(high,{n})-ts_min(low,{n}))", 1, "收盘在滚动高低区间中的位置。"),
        ("rsi_ratio", "上涨幅度占比", "趋势", "ts_mean(max(delta(close,1),0),{n})/ts_mean(abs(delta(close,1)),{n})", 1, "正价格变化占绝对变化的比例，非 Wilder 平滑 RSI。"),
        ("downside_risk", "下行半偏差", "风险", "sqrt(ts_mean(min(returns(close,1),0)*min(returns(close,1),0),{n}))", -1, "负日收益平方均值的平方根，历史风险而非预测。"),
        ("overnight_momentum", "隔夜动量", "动量", "ts_mean(open/lag(close,1)-1,{n})", 1, "开盘相对前收盘收益的平均。"),
        ("intraday_momentum", "日内动量", "动量", "ts_mean((close-open)/open,{n})", 1, "收盘相对开盘收益的平均。"),
        ("breakout", "前期高点突破", "趋势", "close/lag(ts_max(high,{n}),1)-1", 1, "相对截至昨日滚动最高价，显式滞后。"),
        ("drawdown", "距区间高点", "风险", "close/ts_max(close,{n})-1", 1, "收盘相对滚动最高收盘价；接近高点值较高。"),
        ("rebound", "距区间低点", "反转", "close/ts_min(close,{n})-1", -1, "距滚动低点的反向排序假设。"),
        ("trend_efficiency", "方向路径效率", "趋势", "delta(close,{n})/ts_sum(abs(delta(close,1)),{n})", 1, "净价格变化除以路径绝对变化。"),
        ("trend_persistence", "上涨日占比", "趋势", "ts_mean(sign(max(returns(close,1),0)),{n})", 1, "严格正收益日占滚动窗口比例。"),
        ("mean_return", "平均日收益", "动量", "ts_mean(returns(close,1),{n})", 1, "日简单收益算术均值。"),
        ("historical_sharpe", "历史收益波动比", "风险", "ts_mean(returns(close,1),{n})/ts_std(returns(close,1),{n})", 1, "历史均收益除以标准差，未年化，不是预测收益。"),
        ("bollinger_position", "均价标准化偏离", "趋势", "(close-ts_mean(close,{n}))/ts_std(close,{n})", 1, "收盘偏离均值的历史价格标准差倍数。"),
        ("parkinson_volatility", "高低价波动估计", "风险", "sqrt(ts_mean(log(high/low)*log(high/low),{n})/(4*log(2)))", -1, "Parkinson 高低价估计，依赖连续价格近似。"),
        ("max_return", "最大日收益", "反转", "ts_max(returns(close,1),{n})", -1, "滚动最大单日收益的反向排序假设。"),
        ("min_return", "最差日收益", "风险", "ts_min(returns(close,1),{n})", 1, "滚动最差收益，较少极端损失得分较高。"),
        ("return_dispersion", "绝对收益均值", "风险", "ts_mean(abs(returns(close,1)),{n})", -1, "平均绝对日收益。"),
        ("volume_rank", "成交量时序位置", "流动性", "ts_rank(vol,{n})", 1, "今日成交量在过去窗口内的百分位。"),
        ("amount_surge", "相对成交额", "流动性", "amount/ts_mean(amount,{n})-1", 1, "当前原始成交额相对滚动均额。"),
        ("money_flow", "收盘位置量能", "量价", "ts_sum(((2*close-high-low)/(high-low))*vol,{n})/ts_sum(vol,{n})", 1, "成交量加权收盘区间位置，零振幅保留缺失。"),
        ("volume_weighted_return", "成交量加权收益", "量价", "ts_sum(returns(close,1)*vol,{n})/ts_sum(vol,{n})", 1, "日收益按当日成交量加权。"),
        ("moving_average_change", "均线变化", "趋势", "ts_mean(close,{n})/lag(ts_mean(close,{n}),{n})-1", 1, "当前均线相对前一个完整窗口均线。"),
    ]
    for family, label, category, formula, direction, description in families:
        for n in WINDOWS:
            add(f"{family}_{n}", f"{n}日{label}", category, formula.format(n=n), direction, description, family, n)
    for family, n in (("reversal", 1), ("rsi_ratio", 14)):
        row = next(f for f in families if f[0] == family)
        add(f"{family}_{n}", f"{n}日{row[1]}", row[2], row[3].format(n=n), row[4], row[5], family, n)
    add("intraday_strength", "日内实体强度", "量价", "(close-open)/(high-low)", 1, "收开盘价差相对日内振幅，无振幅时缺失。", "intraday_strength")
    add("gap_reversal", "隔夜跳空反转", "反转", "open/lag(close,1)-1", -1, "今日开盘相对前收盘收益的反向。", "gap_reversal", 1)
    add("amplitude_1", "当日振幅", "风险", "(high-low)/close", -1, "当日高低价差除以收盘。", "amplitude", 1)

    basic_recipes = [
        ("earnings_yield", "滚动盈利收益率", "价值", "1/pe_ttm", 1),
        ("book_yield", "账面价值比", "价值", "1/pb", 1),
        ("sales_yield", "滚动销售收益率", "价值", "1/ps_ttm", 1),
        ("dividend_yield", "滚动股息率", "价值", "dv_ttm", 1),
        ("small_size", "小市值", "规模", "log(total_mv)", -1),
        ("small_float_size", "小流通市值", "规模", "log(circ_mv)", -1),
        ("turnover", "换手率", "流动性", "turnover_rate", -1),
        ("free_turnover", "自由流通换手率", "流动性", "turnover_rate_f", -1),
        ("volume_ratio", "量比", "流动性", "volume_ratio", 1),
        ("float_fraction", "流通股本占比", "规模", "float_share/total_share", 1),
        ("free_float_fraction", "自由流通股本占比", "规模", "free_share/total_share", 1),
        ("valuation_spread", "静态滚动估值差", "价值", "pe/pe_ttm-1", 1),
    ]
    for family, name, category, formula, direction in basic_recipes:
        add(family, name, category, formula, direction, "需要真实 daily_basic 字段；估值零分母/非法对数缺失。方向是研究假设，非投资结论。", family)
        for n in (5, 20, 60, 120):
            add(f"{family}_smooth_{n}", f"{n}日{name}均值", category, f"ts_mean({formula},{n})", direction, "对已发布日度字段滚动平滑；不修复原始数据的历史修订或可知时点缺陷。", family, n)
    for field in ("turnover_rate", "turnover_rate_f", "volume_ratio", "pe_ttm", "pb", "total_mv"):
        for n in (20, 60, 120):
            add(f"{field}_relative_{n}", f"{n}日{fields[field]['name']}相对变化", "基本面变化", f"{field}/lag({field},{n})-1", -1 if field in {"pe_ttm", "pb"} else 1, "与历史同口径日度字段比较；有缺失或零分母则缺失。", "daily_basic_change", n)

    for field, metadata in fields.items():
        if metadata["dataset"] != "fina_indicator":
            continue
        direction = -1 if field == "fd_debt_to_assets" else 1
        category = "财务增长" if field.endswith("_yoy") else "财务质量"
        add(field, metadata["name"], category, field, direction, "最近已披露报告期指标，公告后首个交易日生效。报告期与单位按原字段保留，不自动当作 TTM。", "financial_level")
        for n in (20, 60):
            add(f"{field}_change_{n}", f"{n}日{metadata['name']}已知变化", "财务变化", f"delta({field},{n})", direction, "当前已知财务值减去若干交易日前已知值；不是未经计算的财季同比，不回填未来公告。", "financial_disclosed_change", n)
    for field, name in (("fd_eps", "披露期盈利价格比"), ("fd_bps", "披露期账面价格比"), ("fd_ocfps", "披露期现金流价格比")):
        add(field + "_price_yield", name, "财务价值", f"{field}/raw_close", 1, "已披露每股指标除以当日原价；非自动年化/TTM，拆股口径可能不一致，需研究者核验。", "financial_price_ratio")

    models = [{"id": ident, **{k: v for k, v in spec.items() if k != "grid"}, "parameterConfigurations": len(spec["grid"]), "parameters": spec["grid"]} for ident, spec in MODEL_REGISTRY.items()]
    return {"schemaVersion": 2, "recipeLibraryVersion": "0.2.0", "factors": factors, "models": models, "targets": list(TARGETS.values()), "fieldRegistry": list(fields.values()), "industrySources": json.loads(Path(__file__).with_name("industry_sources.json").read_text()), "externalRecipeTemplates": [{**recipe, "dataType": "number", "requiresPointInTimeObservations": True, "providesData": False, "researchStatus": "UNVALIDATED_HYPOTHESIS"} for recipe in EXTERNAL_RECIPE_TEMPLATES], "externalFieldContract": {"aliasPattern": "^(pcd|fd|ext|model)_[a-z0-9_]{1,60}$", "requiredCompanion": "<alias>__available_date", "provenanceMap": "externalFields", "availabilityPolicy": "point_in_time_asof", "inventoryIsNotCoverage": True, "numericOnly": True}, "summary": {"recipes": len(factors), "families": len(set(f["family"] for f in factors)), "ohlcvRecipes": sum(f["dataRequirement"] == "ohlcv" for f in factors), "dailyBasicRecipes": sum(f["dataRequirement"] == "daily_basic" for f in factors), "financialPITRecipes": sum(f["dataRequirement"] == "financial_pit" for f in factors), "namedIndexRecipes": sum(f["dataRequirement"] == "named_index_history" for f in factors), "modelFamilies": len(models), "parameterConfigurations": sum(len(m["grid"]) for m in MODEL_REGISTRY.values()), "rawFieldsAreNotFactors": True}}


if __name__ == "__main__":
    destination = Path(__file__).with_name("catalog.json")
    catalog = build_catalog()
    destination.write_text(json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps(catalog["summary"], ensure_ascii=False))
