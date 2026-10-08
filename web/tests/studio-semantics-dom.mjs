/** Real input/click events; transport is an explicit test double. No AI call.
 * jsdom verifies behavior and structure, not visual layout or mobile fit.
 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import { build } from "esbuild";
import { JSDOM } from "jsdom";
import {
  describeExpression,
  validateExpression,
} from "../../edge/factor-language.mjs";

const dom = new JSDOM(
  '<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>',
  {
    url: "http://localhost/quant/#quant/studio/code",
    runScripts: "outside-only",
    pretendToBeVisual: true,
  },
);
const w = dom.window;
w.scrollTo = () => {};
w.matchMedia = () => ({ matches: true, addEventListener() {} });
w.structuredClone = structuredClone;
const tick = () => new Promise((resolve) => setTimeout(resolve, 35));
const hash = (code) => createHash("sha256").update(code).digest("hex");
let release = null,
  blocked = "",
  failure = "",
  reviewCalls = 0;
function hold(path) {
  blocked = path;
  const wait = new Promise((resolve) => {
    release = resolve;
  });
  return wait;
}
let wait = null;
w.fetch = async (url, options = {}) => {
  const path = String(url),
    data = options.body ? JSON.parse(options.body) : {};
  if (blocked && path.includes(blocked)) await wait;
  if (failure && path.includes(failure))
    throw Error("LOCAL TEST NETWORK FAILURE");
  let response = { items: [], total: 0 };
  if (path.endsWith("/expressions/lint")) {
    try {
      response = {
        valid: true,
        ...validateExpression(data.expression),
        expression: data.expression,
        codeSha256: hash(data.expression),
        deterministicFacts: describeExpression(data.expression),
        availability: {
          status: "ready",
          reason: "Runtime data coverage not verified by this fixture",
        },
      };
    } catch (error) {
      response = {
        valid: false,
        expression: data.expression,
        fields: [],
        lookback: null,
        availability: { status: "unavailable" },
        diagnostics: [{ message: error.message }],
        deterministicFacts: {
          status: "invalid",
          expression: data.expression,
          executionPerformed: false,
        },
      };
    }
  }
  if (path.endsWith("/code/review")) {
    reviewCalls++;
    response = {
      review: {
        mode: data.mode,
        providerExecuted: data.mode === "ai",
        provider: "EXPLICIT TEST DOUBLE",
        model: "WRONG OPINION FIXTURE",
        correctnessCertified: false,
        codeSha256: hash(data.code),
        deterministicFacts: describeExpression(data.code),
        summary: "这是 20 天前的收益率。",
        findings: [],
        patches: [
          {
            title: "测试建议",
            before: data.code,
            after: "lag(close,20)",
            reason: "This is only a test suggestion",
          },
        ],
      },
    };
  }
  return { ok: true, status: 200, text: async () => JSON.stringify(response) };
};
const bundle = await build({
  entryPoints: ["web/main.js"],
  bundle: true,
  write: false,
  format: "iife",
  plugins: [
    {
      name: "test-without-startup",
      setup(b) {
        b.onLoad({ filter: /\/web\/app\.js$/ }, async (file) => ({
          loader: "js",
          contents: (await fs.readFile(file.path, "utf8")).replace(
            "  init();",
            "  window.qa={state,studio,workspace,parseRoute,render};",
          ),
        }));
      },
    },
  ],
});
w.eval(bundle.outputFiles[0].text);
const q = w.qa;
q.state.loading = false;
q.state.session = {
  capabilities: { tushareHosted: false },
  runner: { online: false },
};
q.parseRoute();
q.render();
await tick();
const editor = () => w.document.querySelector("#v2-code-editor");
const facts = () => w.document.querySelector("#v2-dsl-facts");
const click = async (name) => {
  const button = w.document.querySelector(`[data-v2="${name}"]`);
  assert(button, name);
  button.click();
  await tick();
};
function edit(code) {
  const el = editor();
  el.focus();
  el.value = code;
  el.dispatchEvent(new w.InputEvent("input", { bubbles: true }));
  return el;
}

assert.equal(w.document.querySelectorAll("h1").length, 1);
edit("returns(close,20)");
await click("lint-code");
assert.match(facts().textContent, /x\[t\] \/ x\[t−20\] − 1/);
assert.match(facts().textContent, /累计相对变化/);
assert.match(facts().textContent, /完整市场交易日网格/);
assert.match(facts().textContent, /收盘后/);
assert.match(facts().textContent, /未运行数据/);
assert(facts().textContent.includes(hash("returns(close,20)")));
assert.equal(editor().value, "returns(close,20)");

await click("review-ai");
assert.equal(reviewCalls, 1);
assert.match(
  w.document.querySelector(".v2-review-summary").textContent,
  /20 天前的收益率/,
);
assert.match(facts().textContent, /累计相对变化/);
assert.match(
  w.document.querySelector(".v2-review-panel").textContent,
  /不是正确性证明/,
);
assert(!w.document.querySelector(".v2-review-panel .v2-status.ready"));
assert.equal(editor().value, "returns(close,20)", "AI cannot apply its patch");

const changedEditor = edit("returns(close,10)");
assert.equal(
  w.document.activeElement,
  changedEditor,
  "updating evidence preserves typing focus",
);
assert.match(facts().textContent, /待重新校验/);
assert(!w.document.querySelector("[data-review-stale]").hidden);
assert(
  !w.document
    .querySelector("#v2-code-lint")
    .textContent.includes("表达式语法通过"),
);

wait = hold("/expressions/lint");
await click("lint-code");
assert.match(facts().textContent, /正在读取/);
edit("rank(close)");
blocked = "";
release();
await tick();
assert.match(
  facts().textContent,
  /待重新校验/,
  "late evidence cannot certify changed code",
);
assert.equal(editor().value, "rank(close)");
await click("lint-code");
assert.match(facts().textContent, /同一个市场交易日/);
assert(!facts().textContent.includes("20 天前"));

editor().setSelectionRange(0, 0);
editor().dispatchEvent(
  new w.KeyboardEvent("keydown", {
    key: "Tab",
    bubbles: true,
    cancelable: true,
  }),
);
assert(editor().value.startsWith("    "));
assert.match(
  facts().textContent,
  /待重新校验/,
  "keyboard editing also invalidates old evidence",
);

edit("lag(close,-1)");
await click("lint-code");
assert.match(facts().textContent, /尚未解析/);
assert(!facts().querySelector(".v2-dsl-formula"));

edit("rank(returns(close,20))");
assert.match(
  w.document.querySelector("#v2-code-lint").textContent,
  /旧语法结果/,
);
await click("review-manual");
assert.match(facts().textContent, /同一个市场交易日/);
assert(
  !w.document.querySelector("#v2-code-lint").textContent.includes("旧语法结果"),
  "current successful rule check clears the previous invalid expression warning",
);

edit("returns(close,20)");
failure = "/expressions/lint";
await click("lint-code");
assert.equal(editor().value, "returns(close,20)");
assert(!w.document.querySelector('[data-v2="lint-code"]').disabled);
assert(!facts().textContent.includes("解析成功"));
assert.match(
  w.document.querySelector("#toast-root").textContent,
  /LOCAL TEST NETWORK FAILURE/,
);
failure = "";
await click("review-manual");
assert.match(facts().textContent, /累计相对变化/);
await click("apply-patch");
assert.equal(
  editor().value,
  "lag(close,20)",
  "only the explicit apply button changes code",
);
assert.match(facts().textContent, /待重新校验/);

console.log(
  JSON.stringify({
    dom: "jsdom",
    actualParser: true,
    wrongAiOpinionSeparated: true,
    delayedResponse: true,
    editsInvalidateFacts: true,
    explicitPatch: true,
    visualLayout: "NOT_TESTED",
  }),
);
w.close();
