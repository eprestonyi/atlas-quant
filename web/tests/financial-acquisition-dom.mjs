/** Actual application + DOM events; API doubles, not browser/provider evidence. */
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import { build } from "esbuild";
import { JSDOM } from "jsdom";
const defs = JSON.parse(
  await fs.readFile("edge/financial/definitions.json", "utf8"),
);
const dom = new JSDOM(
    '<div id="app"></div><div id="toast-root"></div><div id="modal-root"></div>',
    {
      url: "http://acquisition.localhost/quant/#quant/studio/financial/acquire/source",
      runScripts: "outside-only",
      pretendToBeVisual: true,
    },
  ),
  w = dom.window;
w.matchMedia = () => ({ matches: false, addEventListener() {} });
w.structuredClone = structuredClone;
w.scrollTo = () => {};
const id = "11111111-1111-4111-8111-111111111111",
  jobId = "22222222-2222-4222-8222-222222222222",
  inputId = "33333333-3333-4333-8333-333333333333";
let enabled = false,
  gate = false,
  release,
  lastPlan,
  savedPlan,
  planCalls = 0,
  startCalls = 0,
  job = {
    id: jobId,
    planId: id,
    status: "running",
    phase: "fetching_sources",
    createdAt: "2026-10-08",
    updatedAt: "2026-10-08",
  };
const paths = [];
w.fetch = async (url, options = {}) => {
  const path = String(url).replace("/quant/api", "");
  paths.push(path);
  let result = { items: [], total: 0 };
  if (path === "/financial/acquisition-capabilities")
    result = { enabled, runner: { online: true } };
  if (path === "/financial/definitions") result = defs;
  if (path === "/financial/acquisition-plans" && options.method === "POST") {
    planCalls++;
    lastPlan = JSON.parse(options.body);
    if (gate) await new Promise((r) => (release = r));
    result = {
      plan: {
        id,
        name: lastPlan.name,
        planRoot: "a".repeat(64),
        selection: lastPlan,
        budget: { maximumProviderCalls: 4, cachedRequests: 0, newRequests: 4 },
        blockedReasons: [],
        requests: [
          {
            endpoint: "trade_cal",
            params: {
              exchange: "SSE",
              start_date: "20250101",
              end_date: "20250430",
            },
            fields: ["cal_date", "is_open"],
            requestKey: "b".repeat(64),
            cache: { status: "missing" },
          },
        ],
      },
    };
  }
  if (path === "/financial/acquisition-plans" && options.method === "POST")
    savedPlan = result.plan;
  if (path === "/financial/acquisition-plans/" + id && !options.method)
    result = { plan: savedPlan };
  if (path.endsWith("/start")) {
    startCalls++;
    result = { job };
  }
  if (path === "/financial/acquisition-jobs/" + jobId) result = { job };
  return { ok: true, status: 200, text: async () => JSON.stringify(result) };
};
const built = await build({
  entryPoints: ["web/main.js"],
  bundle: true,
  write: false,
  format: "iife",
  plugins: [
    {
      name: "no-init",
      setup(b) {
        b.onLoad({ filter: /\/web\/app\.js$/ }, async (a) => ({
          contents: (await fs.readFile(a.path, "utf8")).replace(
            "  init();",
            "  window.qa={state,workspace,parseRoute,render};",
          ),
          loader: "js",
        }));
      },
    },
  ],
});
w.eval(built.outputFiles[0].text);
const q = w.qa;
q.state.loading = false;
q.state.session = { runner: { online: false } };
const tick = () => new Promise((r) => setTimeout(r, 35));
const text = () => w.document.querySelector("main").textContent;
const click = (action) => {
  const el = w.document.querySelector(`[data-acq="${action}"]`);
  assert(el, action);
  el.click();
};
const input = (key, value) => {
  const el = w.document.querySelector(`[data-acq-field="${key}"]`);
  assert(el, key);
  el.value = value;
  el.dispatchEvent(new w.Event("input", { bubbles: true }));
};
const route = async (hash) => {
  w.location.hash = hash;
  await tick();
};
q.parseRoute();
q.render();
await q.workspace.routeChanged();
await tick();
assert.equal(w.document.querySelectorAll("h1").length, 1);
assert(text().includes("尚未开放自助获取"));
assert(w.document.querySelector('[data-acq="plan"]').disabled);
click("plan");
await tick();
assert.equal(planCalls, 0);
assert(
  !paths.some((p) => p === "/financial/inputs/acquire"),
  "acquisition route must not fetch legacy input id",
);
assert(
  w.document.querySelector(
    'a[href="https://github.com/eprestonyi/atlas-quant"]',
  ),
  "new workspace has a visible source link",
);
enabled = true;
q.workspace.financial.acquisition.state.error = "reload";
q.render();
click("reload");
await tick();
input("name", "保留我的源范围");
input("symbols", "600690.SH");
input("year", "2024");
input("announcementStart", "2025-01-01");
input("start", "2025-03-28");
input("end", "2025-04-30");
const chosen = w.document.querySelector("[data-acq-state]");
chosen.checked = true;
chosen.dispatchEvent(new w.Event("change", { bubbles: true }));
// Real checkbox and button affordances are keyboard-operable; no drag is required.
gate = true;
click("plan");
await tick();
assert.equal(planCalls, 1);
input("name", "请求过程中改名");
gate = false;
release();
await tick();
assert(w.location.hash.endsWith("/source"));
assert.equal(w.document.querySelector("#acq-name").value, "请求过程中改名");
click("plan");
await tick();
await tick();
assert(w.location.hash.endsWith("/review/" + id));
assert(text().includes("最多 4 次"));
assert.equal(startCalls, 0, "review never triggers provider acquisition");
q.workspace.financial.acquisition.state.plan = null;
await q.workspace.financial.acquisition.routeChanged();
assert(
  text().includes("最多 4 次"),
  "saved plan reload uses owner API and retained immutable choices",
);

assert.equal(lastPlan.name, "请求过程中改名");
click("start");
await tick();
await tick();
assert.equal(startCalls, 1);
assert(w.location.hash.endsWith("/job/" + jobId));
assert(text().includes("正在获取与冻结"));
assert(!text().includes("准备完成"));
job = {
  ...job,
  status: "completed",
  result: { inputId, researchBinding: false },
};
click("refresh");
await tick();
assert(text().includes("尚未通过财务准备校验"));
assert(
  w.document.querySelector(
    `a[href="#quant/studio/financial/${inputId}/source"]`,
  ),
);
await route("#quant/studio/financial/acquire/source");
assert.equal(w.document.querySelector("#acq-name").value, "请求过程中改名");
// A response received after navigation cannot steal the user's new route.
gate = true;
click("plan");
await tick();
await route("#quant/studio/financial");
gate = false;
release();
await tick();
assert.equal(w.location.hash, "#quant/studio/financial");
q.workspace.financial.dispose();
dom.window.close();
console.log(
  JSON.stringify({
    actualApplicationDOM: true,
    defaultOff: true,
    explicitStart: true,
    draftRetention: true,
    lateResponseRouteGuard: true,
    sourceLink: true,
    providerCalled: false,
    browserVisualAcceptance: false,
  }),
);
