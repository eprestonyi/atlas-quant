/** Fail check/build if the independently shipped execution contract is missing
 * or an operator cannot be explained by the same validator used at the edge.
 * Numerical conformance belongs to engine/tests/test_dsl_semantics.py.
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { DSL_CONTRACT, describeExpression } from "../edge/factor-language.mjs";

export function checkDslContract() {
  const fixture = JSON.parse(
    fs.readFileSync(
      new URL("../tests/fixtures/dsl-conformance.json", import.meta.url),
    ),
  );
  const covered = new Set();
  assert.equal(DSL_CONTRACT.version, "atlas-factor-semantics/v1");
  assert.equal(DSL_CONTRACT.language, "atlas-factor-dsl/v1");
  assert.equal(DSL_CONTRACT.syntax.version, "ascii_tokens_semantic_tree/v1");
  assert.equal(DSL_CONTRACT.syntax.trailingComma, false);
  for (const key of ["treeDepth", "treeNodes", "parenthesisNesting"])
    assert(
      Number.isInteger(DSL_CONTRACT.limits[key]) &&
        DSL_CONTRACT.limits[key] > 0,
      key,
    );
  assert.equal(DSL_CONTRACT.limits.treeRootDepth, 0);
  for (const example of fixture.cases) {
    const facts = describeExpression(example.expression);
    assert.equal(facts.lookback, example.lookback, example.expression);
    for (const operation of facts.operations) covered.add(operation.operator);
  }
  for (const [name, spec] of Object.entries(DSL_CONTRACT.operators)) {
    assert(covered.has(name), `Unexplained DSL operator: ${name}`);
    assert(Number.isInteger(spec.arity) && spec.arity > 0, name);
    for (const field of ["formula", "meaning", "missing", "category"]) {
      assert.equal(typeof spec[field], "string", `${name}.${field}`);
      assert(spec[field].length > 0, `${name}.${field}`);
    }
  }
  return {
    version: DSL_CONTRACT.version,
    operators: Object.keys(DSL_CONTRACT.operators).length,
  };
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  console.log(JSON.stringify(checkDslContract()));
}
