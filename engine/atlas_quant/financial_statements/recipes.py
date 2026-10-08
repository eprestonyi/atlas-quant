"""Sixteen distinct states, not a direction, ranking rule or profitability claim."""

from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from .contracts import FIELDS, FORMULA_VERSION, ContractError, is_quarter_end
from .periods import quarter, ttm, point, average_assets, prior_quarter
from .results import derive, missing


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    anchor_field: str
    numerator: tuple[tuple[str, str, int], ...]
    denominator: tuple[str, str, int]
    subtract_one: bool = False
    applicable_company_types: tuple[str, ...] = ("1",)


I = "income."
B = "balancesheet."
C = "cashflow."


def _recipe(id, name, anchor, numerator, denominator, growth=False):
    return Recipe("model_fin_" + id, name, anchor, tuple(numerator), denominator, growth)


RECIPES = MappingProxyType(
    {
        r.id: r
        for r in [
            _recipe(
                "revenue_quarter_yoy",
                "收入季度同比",
                I + "revenue",
                [("quarter", I + "revenue", 0)],
                ("quarter", I + "revenue", 4),
                True,
            ),
            _recipe(
                "revenue_ttm_yoy",
                "TTM收入同比",
                I + "revenue",
                [("ttm", I + "revenue", 0)],
                ("ttm", I + "revenue", 4),
                True,
            ),
            _recipe(
                "operating_margin",
                "经营利润率",
                I + "revenue",
                [("ttm", I + "operate_profit", 0)],
                ("ttm", I + "revenue", 0),
            ),
            _recipe(
                "parent_net_margin",
                "归母净利率",
                I + "revenue",
                [("ttm", I + "n_income_attr_p", 0)],
                ("ttm", I + "revenue", 0),
            ),
            _recipe(
                "cash_revenue_ratio",
                "经营现金收入比",
                I + "revenue",
                [("ttm", C + "n_cashflow_act", 0)],
                ("ttm", I + "revenue", 0),
            ),
            _recipe(
                "profit_cash_asset_gap",
                "现金利润差",
                I + "n_income",
                [("ttm", I + "n_income", 0), ("subtract_ttm", C + "n_cashflow_act", 0)],
                ("average_assets", B + "total_assets", 0),
            ),
            _recipe(
                "cash_assets_ratio",
                "现金流资产比",
                C + "n_cashflow_act",
                [("ttm", C + "n_cashflow_act", 0)],
                ("average_assets", B + "total_assets", 0),
            ),
            _recipe(
                "capex_revenue_ratio",
                "购建现金支出强度",
                I + "revenue",
                [("ttm", C + "c_pay_acq_const_fiolta", 0)],
                ("ttm", I + "revenue", 0),
            ),
            _recipe(
                "cash_less_capex_assets",
                "经营现金流减购建支出与资产比",
                C + "n_cashflow_act",
                [
                    ("ttm", C + "n_cashflow_act", 0),
                    ("subtract_ttm", C + "c_pay_acq_const_fiolta", 0),
                ],
                ("average_assets", B + "total_assets", 0),
            ),
            _recipe(
                "assets_yoy",
                "资产同比",
                B + "total_assets",
                [("point", B + "total_assets", 0)],
                ("point", B + "total_assets", 4),
                True,
            ),
            _recipe(
                "cash_asset_share",
                "现金资产占比",
                B + "total_assets",
                [("point", B + "money_cap", 0)],
                ("point", B + "total_assets", 0),
            ),
            _recipe(
                "current_coverage",
                "流动性覆盖",
                B + "total_assets",
                [("point", B + "total_cur_assets", 0)],
                ("point", B + "total_cur_liab", 0),
            ),
            _recipe(
                "liability_asset_share",
                "账面负债占比",
                B + "total_assets",
                [("point", B + "total_liab", 0)],
                ("point", B + "total_assets", 0),
            ),
            _recipe(
                "borrowings_asset_share",
                "短长借款与资产比",
                B + "total_assets",
                [("point", B + "st_borr", 0), ("point", B + "lt_borr", 0)],
                ("point", B + "total_assets", 0),
            ),
            _recipe(
                "receivable_asset_share",
                "应收账款资产占比",
                B + "total_assets",
                [("point", B + "accounts_receiv", 0)],
                ("point", B + "total_assets", 0),
            ),
            _recipe(
                "goodwill_asset_share",
                "商誉资产占比",
                B + "total_assets",
                [("point", B + "goodwill", 0)],
                ("point", B + "total_assets", 0),
            ),
        ]
    }
)


def compute_states(
    store, symbol, as_of, ids=None, *, period_end=None, scope="consolidated", flow_basis="ytd"
):
    if scope not in {"consolidated", "parent"} or flow_basis not in {"ytd", "quarter"}:
        raise ContractError("explicit supported scope and flow basis required")
    ids = tuple(RECIPES) if ids is None else tuple(ids)
    if len(set(ids)) != len(ids) or any(id not in RECIPES for id in ids):
        raise ContractError("unknown or duplicate financial state")
    result = {}
    for id in ids:
        recipe = RECIPES[id]
        anchor_basis = "point" if FIELDS[recipe.anchor_field].period_kind == "point" else flow_basis
        period = period_end or store.latest_period(
            symbol, recipe.anchor_field, as_of, scope=scope, basis=anchor_basis
        )
        args = {
            "period_end": period,
            "as_of": as_of,
            "scope": scope,
            "basis": "ratio",
            "formula": FORMULA_VERSION + ":" + id,
            "unit": "ratio",
            "calendar_evidence": store.calendar_evidence,
        }
        issue = store.observation_issue(as_of)
        if issue or period is None:
            result[id] = missing(issue or "NO_AVAILABLE_ANCHOR_PERIOD", **args)
            continue
        if not is_quarter_end(period):
            result[id] = missing("UNSUPPORTED_REPORT_PERIOD", **args)
            continue

        def evaluate(leg):
            kind, field, lag = leg
            target = prior_quarter(period, lag)
            if kind == "point":
                return point(store, symbol, field, target, as_of, scope=scope)
            if kind == "average_assets":
                return average_assets(store, symbol, target, as_of, scope=scope)
            function = quarter if kind == "quarter" else ttm
            return function(store, symbol, field, target, as_of, scope=scope, flow_basis=flow_basis)

        values = [evaluate(leg) for leg in (*recipe.numerator, recipe.denominator)]
        reasons = set()
        if values[-1].status == "ok" and values[-1].value <= 0:
            reasons.add("DENOMINATOR_NONPOSITIVE")
        if any(
            d.company_type not in recipe.applicable_company_types
            for value in values
            for d in value.dependencies
        ):
            reasons.add("UNSUPPORTED_COMPANY_TYPE")

        def formula(*numbers):
            top = sum(
                (
                    -value if leg[0].startswith("subtract_") else value
                    for leg, value in zip(recipe.numerator, numbers[:-1])
                ),
                Decimal(0),
            )
            return top / numbers[-1] - (Decimal(1) if recipe.subtract_one else Decimal(0))

        result[id] = derive(values, formula, reasons=reasons, **args)
    return result
