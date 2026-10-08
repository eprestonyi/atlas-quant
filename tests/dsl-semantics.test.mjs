import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { spawnSync } from "node:child_process";
import {
  DSL_CONTRACT,
  describeExpression,
  validateExpression,
} from "../edge/factor-language.mjs";

const fixture = JSON.parse(
  fs.readFileSync(new URL("./fixtures/dsl-conformance.json", import.meta.url)),
);
const catalog = JSON.parse(
  fs.readFileSync(
    new URL("../engine/atlas_quant/catalog.json", import.meta.url),
  ),
);

test("all shared operator examples and catalog recipes use the real Python parser metadata", () => {
  const expressions = [
    ...new Set([
      ...fixture.cases.map((c) => c.expression),
      ...catalog.factors.map((f) => f.expression),
    ]),
  ];
  const script =
    "import sys,json\nfrom atlas_quant.factors import validate_expression\nprint(json.dumps([validate_expression(x) for x in json.load(sys.stdin)]))";
  const process = spawnSync(".venv/bin/python", ["-c", script], {
    input: JSON.stringify(expressions),
    encoding: "utf8",
    env: {
      ...globalThis.process.env,
      PYTHONPATH: "engine",
      PYTHONDONTWRITEBYTECODE: "1",
    },
  });
  assert.equal(process.status, 0, process.stderr);
  const actual = JSON.parse(process.stdout);
  for (let i = 0; i < expressions.length; i++) {
    const edge = validateExpression(expressions[i]);
    assert.equal(edge.lookback, actual[i].lookback, expressions[i]);
    assert.deepEqual(edge.fields, actual[i].fields, expressions[i]);
  }
  for (const item of fixture.cases)
    assert.equal(describeExpression(item.expression).lookback, item.lookback);
  const explained = new Set(
    fixture.cases.flatMap((c) =>
      describeExpression(c.expression).operations.map((o) => o.operator),
    ),
  );
  for (const name of Object.keys(DSL_CONTRACT.operators))
    assert(explained.has(name), name);
});

test("cumulative change, lagged value, rolling and cross-section facts remain separate", () => {
  const returns = describeExpression("returns(close,20)"),
    lag = describeExpression("lag(close,20)");
  assert.match(returns.operations[0].formula, /x\[t\] \/ x\[t−n\] − 1/);
  assert.equal(lag.operations[0].formula, "x[t−n]");
  assert.equal(returns.operations[0].window, 20);
  assert.match(returns.evaluation.timeMeaning, /完整市场交易日网格/);
  assert.match(returns.evaluation.availability, /收盘后/);
  assert.match(returns.evaluation.horizon, /单独定义/);
  assert.match(
    describeExpression("rank(close)").operations[0].meaning,
    /同一个市场交易日/,
  );
  assert.match(
    describeExpression("ts_rank(close,3)").operations[0].meaning,
    /同一标的/,
  );
  assert.equal(returns.executionPerformed, false);
  assert.equal(returns.correctnessCertified, false);
  for (const expression of fixture.invalid)
    assert.throws(() => describeExpression(expression), undefined, expression);
});

test("the shared ASCII grammar and semantic tree limits are explicit admission rules", () => {
  const balanced = (leaves) =>
    leaves === 1
      ? "pe"
      : `(${balanced(Math.floor(leaves / 2))}+${balanced(Math.ceil(leaves / 2))})`;
  const valid = [
    "close\n+1",
    "close\r\n+1",
    "lag(close, +1)",
    "close+.5e1",
    "-".repeat(16) + "close",
    "sign(".repeat(16) + "close" + ")".repeat(16),
    "(".repeat(128) + "close" + ")".repeat(128),
    "-" + balanced(64), // Exactly 128 semantic nodes.
  ];
  const invalid = [
    "lag(close,01)",
    "close+00.5",
    "close+0x10",
    "close+1_000",
    "ｃｌｏｓｅ",
    "close # comment",
    "close\u00a0+1",
    "lag(close,1,)",
    "close + 1 2",
    "-".repeat(17) + "close",
    Array(18).fill("close").join("+"),
    "(".repeat(129) + "close" + ")".repeat(129),
    "close+" + "9".repeat(100),
    balanced(65), // 129 semantic nodes with shallow depth and <500 characters.
  ];
  for (const expression of valid)
    assert.doesNotThrow(() => validateExpression(expression), expression);
  for (const expression of invalid)
    assert.throws(() => validateExpression(expression), undefined, expression);
  const script = `import sys,json
from atlas_quant.factors import validate_expression,FactorError
results=[]
for expression in json.load(sys.stdin):
 try:
  validate_expression(expression)
  results.append(True)
 except FactorError:
  results.append(False)
print(json.dumps(results))`;
  const result = spawnSync(".venv/bin/python", ["-c", script], {
    input: JSON.stringify([...valid, ...invalid]),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "engine", PYTHONDONTWRITEBYTECODE: "1" },
  });
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(result.stdout), [
    ...valid.map(() => true),
    ...invalid.map(() => false),
  ]);
  assert.equal(DSL_CONTRACT.limits.treeRootDepth, 0);
  assert.equal(DSL_CONTRACT.syntax.trailingComma, false);
});
