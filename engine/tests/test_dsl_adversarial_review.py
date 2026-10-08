"""Independent parser-boundary and causal-grid review, without provider calls.

These tests exercise the real edge and Python validators. Acceptance policy may
be tightened, but a shared language cannot admit an expression on only one side.
"""

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from atlas_quant.factors import FactorError, evaluate_expression, validate_expression


ROOT = Path(__file__).resolve().parents[2]
BOUNDARIES = [
    "lag(close,01)",
    "close+01",
    "close\u00a0+1",
    "close\n+1",
    "(close\n+1)",
    "close+0x10",
    "close+1_000",
    "ｃｌｏｓｅ",
    "close # ignored text",
    "close + \\\n1",
    "lag(close,1,)",
    "+".join(["close"] * 18),
    "+".join(["close"] * 32),
    "sign(" * 16 + "close" + ")" * 16,
    "(" * 20 + "close" + ")" * 20,
    "(" * 128 + "close" + ")" * 128,
    "(" * 129 + "close" + ")" * 129,
    "-" * 16 + "close",
    "-" * 17 + "close",
    "lag(close,+1)",
    "clip(close,-1.5,2e1)",
    "returns(lag(close,252),252)",
    "returns(lag(close,252),253)",
]
UNSUPPORTED = [
    "lag(close,-1)",
    "lag(close,--1)",
    "lag(close,1+1)",
    "lag(close,True)",
    "lag(close,n=1)",
    "close.__class__",
    "close[0]",
    "close**2",
    "close//2",
    "close%2",
    "close if close else vol",
    "(lambda: close)()",
    "__import__('os')",
    "[close for close in vol]",
    "close;vol",
    "future(close,1)",
]


@pytest.fixture(scope="module")
def edge_results():
    script = """
import fs from 'node:fs';
import {validateExpression, describeExpression} from './edge/factor-language.mjs';
const inputs = JSON.parse(fs.readFileSync(0, 'utf8'));
const output = inputs.map(expression => {
  try {
    const parsed = validateExpression(expression);
    const facts = describeExpression(expression);
    return {valid: true, fields: parsed.fields, lookback: parsed.lookback,
      status: facts.status, original: facts.expression,
      executionPerformed: facts.executionPerformed};
  } catch (error) { return {valid: false, error: error.message}; }
});
process.stdout.write(JSON.stringify(output));
"""
    expressions = BOUNDARIES + UNSUPPORTED
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(expressions),
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=True,
        timeout=20,
    )
    return dict(zip(expressions, json.loads(result.stdout), strict=True))


@pytest.mark.parametrize("expression", BOUNDARIES)
def test_edge_evidence_and_execution_parser_admit_same_language(expression, edge_results):
    edge = edge_results[expression]
    try:
        parsed = validate_expression(expression)
    except FactorError as error:
        assert not edge["valid"], (
            f"Studio certifies a parsed tree but the execution parser rejects it: {error}"
        )
        return
    assert edge["valid"], f"Execution parser accepts but Studio rejects: {edge.get('error')}"
    assert edge["fields"] == parsed["fields"]
    assert edge["lookback"] == parsed["lookback"]
    assert edge["status"] == "parsed"
    assert edge["original"] == expression
    assert edge["executionPerformed"] is False


@pytest.mark.parametrize("expression", UNSUPPORTED)
def test_unsupported_syntax_cannot_obtain_a_deterministic_fact_tree(expression, edge_results):
    assert edge_results[expression]["valid"] is False
    with pytest.raises(FactorError):
        validate_expression(expression)


def synthetic_grid():
    index = pd.MultiIndex.from_product(
        [pd.date_range("2024-01-01", periods=40, freq="B"), ["A", "B", "C"]],
        names=["trade_date", "ts_code"],
    )
    position = np.arange(len(index), dtype=float)
    close = 100 + position / 4 + np.sin(position / 3)
    return pd.DataFrame({"close": close, "vol": 10 + position % 7}, index=index)


@pytest.mark.parametrize(
    "expression",
    [
        "returns(lag(close,2),3)",
        "ts_mean(returns(close,2),4)",
        "ts_rank(close,5)",
        "rank(returns(close,3))",
        "zscore(ts_std(close,4))",
        "clip(log(close),1,10)+sqrt(vol)",
    ],
)
def test_future_perturbation_cannot_change_current_or_past_outputs(expression):
    original = synthetic_grid()
    cutoff = original.index.levels[0][23]
    changed = original.copy()
    changed.loc[changed.index.get_level_values("trade_date") > cutoff, :] = 987654.0
    before = evaluate_expression(expression, original)
    after = evaluate_expression(expression, changed)
    mask = original.index.get_level_values("trade_date") <= cutoff
    pd.testing.assert_series_equal(before.loc[mask], after.loc[mask], check_exact=True)


def test_nested_endpoint_and_rolling_window_do_not_compress_a_missing_session():
    data = synthetic_grid()
    dates = data.index.levels[0]
    data.loc[(dates[20], "A"), "close"] = np.nan
    # At t=24, returns(lag(x,2),3) uses x[22]/x[19], skipping no grid positions.
    actual = evaluate_expression("returns(lag(close,2),3)", data)
    expected = data.loc[(dates[22], "A"), "close"] / data.loc[(dates[19], "A"), "close"] - 1
    assert actual.loc[(dates[24], "A")] == pytest.approx(expected)
    assert pd.isna(evaluate_expression("ts_mean(close,5)", data).loc[(dates[24], "A")])
    assert validate_expression("returns(lag(close,2),3)")["lookback"] == 5
