import contract from "../engine/atlas_quant/dsl_contract.json" with { type: "json" };
import { ApiError } from "./errors.mjs";

export const DSL_CONTRACT = contract;
export const FIELDS = new Set(contract.fields);
const limits = contract.limits;
const fail = (message) => {
  throw new ApiError("INVALID_INPUT", message);
};

/** The existing edge validator now retains its parsed tree for explanations.
 * There is no second parser, regex interpreter, or AI-derived operator meaning.
 */
export function parseExpression(expression) {
  if (
    typeof expression !== "string" ||
    !expression.trim() ||
    expression.length > limits.expressionLength
  ) {
    fail(`因子表达式需为 1–${limits.expressionLength} 字符`);
  }
  // Keep the source intact: trim() would silently admit Unicode whitespace.
  const text = expression;
  const tokenPattern = new RegExp(`^(?:${contract.syntax.tokenPattern})`);
  let pos = 0,
    nesting = 0,
    index = 0;
  const fields = new Set(),
    tokens = [];
  while (pos < text.length) {
    if (contract.syntax.whitespace.includes(text[pos])) {
      pos++;
      continue;
    }
    const match = tokenPattern.exec(text.slice(pos));
    if (!match) fail("表达式包含不支持的字符；仅支持 ASCII 因果数学运算");
    if (match[0] === "(" && ++nesting > limits.parenthesisNesting)
      fail("表达式括号嵌套过深");
    if (match[0] === ")") nesting--;
    tokens.push(match[0]);
    pos += match[0].length;
  }
  const peek = () => tokens[index];
  const take = () => tokens[index++];
  const expect = (token) => {
    if (take() !== token) fail("表达式括号或参数格式无效");
  };
  function binary(kind, left, right) {
    return {
      kind: "binary",
      operator: kind,
      left,
      right,
      lookback: Math.max(left.lookback, right.lookback),
    };
  }
  function expressionNode() {
    let value = term();
    while (["+", "-"].includes(peek())) {
      const operator = take();
      value = binary(operator, value, term());
    }
    return value;
  }
  function term() {
    let value = atom();
    while (["*", "/"].includes(peek())) {
      const operator = take();
      value = binary(operator, value, atom());
    }
    return value;
  }
  function atom() {
    const token = take();
    if (!token) fail("表达式不完整");
    if (token === "+" || token === "-") {
      const operand = atom();
      return {
        kind: "unary",
        operator: token,
        operand,
        lookback: operand.lookback,
        ...(operand.kind === "number"
          ? { literal: (token === "-" ? -1 : 1) * operand.literal }
          : {}),
      };
    }
    if (token === "(") {
      const value = expressionNode();
      expect(")");
      return value;
    }
    if (/^\d|^\./.test(token)) {
      const literal = Number(token);
      if (!Number.isFinite(literal) || Math.abs(literal) > limits.numberAbsMax)
        fail("数字常量超出范围");
      return { kind: "number", literal, lookback: 0 };
    }
    if (peek() !== "(") {
      if (
        !FIELDS.has(token) &&
        !/^(?:pcd|fd|ext|model)_[a-z0-9_]{1,60}$/.test(token)
      )
        fail(`不支持的数据字段：${token}`);
      fields.add(token);
      return { kind: "field", name: token, lookback: 0 };
    }
    take();
    const args = [];
    if (peek() !== ")") {
      do {
        args.push(expressionNode());
        if (peek() !== ",") break;
        take();
      } while (true);
    }
    expect(")");
    const operator = contract.operators[token];
    if (!operator) fail(`不支持的因子函数：${token}`);
    if (args.length !== operator.arity)
      fail(`${token} 需要 ${operator.arity} 个参数`);
    let lookback = Math.max(...args.map((arg) => arg.lookback));
    if (operator.category === "window") {
      const window = args[1].literal;
      if (
        !Number.isInteger(window) ||
        window < limits.windowMin ||
        window > limits.windowMax
      )
        fail(`${token} 的窗口必须是 1–252 的正整数`);
      lookback = args[0].lookback + window + operator.lookbackOffset;
    }
    if (operator.category === "clip") {
      if (
        args[1].literal === undefined ||
        args[2].literal === undefined ||
        args[1].literal >= args[2].literal
      )
        fail("clip 需要表达式与递增的数值上下限");
      lookback = args[0].lookback;
    }
    return { kind: "call", name: token, args, lookback };
  }
  const tree = expressionNode();
  if (index !== tokens.length) fail("表达式包含多余内容");
  // Count the same semantic nodes as Python, not recursive parser productions.
  let nodes = 0;
  const pending = [[tree, limits.treeRootDepth]];
  while (pending.length) {
    const [node, depth] = pending.pop();
    if (++nodes > limits.treeNodes || depth > limits.treeDepth)
      fail("表达式过于复杂");
    const children =
      node.kind === "call"
        ? node.args
        : node.kind === "binary"
          ? [node.left, node.right]
          : node.kind === "unary"
            ? [node.operand]
            : [];
    for (const child of children) pending.push([child, depth + 1]);
  }
  if (!fields.size) fail("因子必须引用至少一个数据字段");
  if (tree.lookback > limits.lookbackMax)
    fail("因子总回看窗口不可超过 504 个交易日");
  return {
    fields: [...fields].sort(),
    lookback: tree.lookback,
    validation: "syntax_validated",
    language: contract.language,
    tree,
  };
}

export function validateExpression(expression) {
  const { tree, ...metadata } = parseExpression(expression);
  return metadata;
}

export function formatExpressionNode(node) {
  if (node.kind === "number") return String(node.literal);
  if (node.kind === "field") return node.name;
  if (node.kind === "unary")
    return `${node.operator}(${formatExpressionNode(node.operand)})`;
  if (node.kind === "binary")
    return `(${formatExpressionNode(node.left)} ${node.operator} ${formatExpressionNode(node.right)})`;
  return `${node.name}(${node.args.map(formatExpressionNode).join(", ")})`;
}

/** Facts are bounded by the validated expression's own node/character limits. */
export function describeExpression(expression) {
  const parsed = parseExpression(expression),
    operations = [];
  function visit(node) {
    if (node.kind === "call") {
      node.args.forEach(visit);
      const spec = contract.operators[node.name];
      operations.push({
        operator: node.name,
        expression: formatExpressionNode(node),
        title: spec.title,
        formula: spec.formula,
        meaning: spec.meaning,
        missing: spec.missing,
        category: spec.category,
        lookback: node.lookback,
        inputs: node.args.map(formatExpressionNode),
        ...(spec.category === "window"
          ? {
              window: node.args[1].literal,
              includesCurrent: !["lag"].includes(node.name),
              directGridSpan:
                node.args[1].literal + (spec.lookbackOffset === 0 ? 1 : 0),
            }
          : {}),
      });
    } else if (node.kind === "binary") {
      visit(node.left);
      visit(node.right);
      operations.push({
        operator: node.operator,
        expression: formatExpressionNode(node),
        ...contract.arithmetic[node.operator],
        title: contract.arithmetic[node.operator].meaning,
        category: "arithmetic",
        lookback: node.lookback,
        inputs: [
          formatExpressionNode(node.left),
          formatExpressionNode(node.right),
        ],
      });
    } else if (node.kind === "unary") {
      visit(node.operand);
      operations.push({
        operator: node.operator === "-" ? "negate" : "positive",
        expression: formatExpressionNode(node),
        title: node.operator === "-" ? "取相反数" : "显式正号",
        formula: `${node.operator}x[t]`,
        meaning:
          node.operator === "-" ? "将输入逐值取相反数。" : "保留输入数值。",
        missing: "输入缺值则缺失。",
        category: "unary",
        lookback: node.lookback,
        inputs: [formatExpressionNode(node.operand)],
      });
    }
  }
  visit(parsed.tree);
  return {
    status: "parsed",
    source: "shared_dsl_contract_and_validator",
    contractVersion: contract.version,
    expression,
    canonicalExpression: formatExpressionNode(parsed.tree),
    fields: parsed.fields,
    lookback: parsed.lookback,
    operations,
    evaluation: contract.evaluation,
    syntaxOnly: true,
    executionPerformed: false,
    dataCoverageVerified: false,
    correctnessCertified: false,
    predictiveValueEstablished: false,
  };
}
