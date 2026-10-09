/** Local visual fixture server. NOT the Worker, storage, or performance acceptance path. */
import http from 'node:http';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { diagnosticSummary } from '../../edge/statistical-quant/summaries.mjs';
import { loadWebAssets } from '../../scripts/worker-source.mjs';

if (!process.argv[2])
  throw Error('Usage: node web/tests/report-preview.mjs /private/existing-report.json [port]');
const web = fileURLToPath(new URL('../', import.meta.url));
const input = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const report = input.result || input.report || input;
const assets = await loadWebAssets(web);
const catalog = JSON.parse(await fs.readFile(new URL('../../engine/atlas_quant/catalog.json', import.meta.url), 'utf8'));
const functionFixtures = JSON.parse(await fs.readFile(new URL('../../engine/tests/fixtures/model-function-golden-v1.json', import.meta.url), 'utf8')).cases;
const visualBanner = '<div style="position:fixed;bottom:8px;right:8px;z-index:10000;padding:5px 10px;background:#283021;color:#eef0e7;border:1px solid #849271;font:11px monospace" data-visual-fixture>visualFixtureOnly · 冻结数据 / 只读接口模拟</div>';
const bundleId = 'b'.repeat(64);
const source = report.forecasts;
const collections = {
  forecasts: source.rows,
  targets: source.targetDefinitions,
  modelFits: source.modelFits,
  modelSearchCandidates: source.diagnostics.modelSearch?.candidates,
  factorFeatures: source.factorResearch?.diagnostics?.features,
  factorJointDistributions: source.factorResearch?.diagnostics?.dependence?.jointDistributions,
  perTarget: source.diagnostics.perTarget,
  finalTrials: source.diagnostics.finalTrials,
  outerFolds: source.diagnostics.outerFolds,
  baselineRows: source.diagnostics.factorIncrement?.baselineRows,
  dailyLosses: source.diagnostics.factorIncrement?.dailyLosses,
  trades: report.trades,
  equity: report.equity,
  riskLedger: report.execution?.ledger,
  decisions: report.execution?.decisions,
};
const summary = {
  ...report,
  forecasts: { ...source, diagnostics: diagnosticSummary(source.diagnostics) },
  validation: diagnosticSummary(report.validation),
  execution: { ...report.execution },
  selection: { ...report.selection },
};
for (const key of ['rows', 'targetDefinitions', 'modelFits', 'hedgeFits'])
  delete summary.forecasts[key];
if (source.factorResearch) summary.forecasts.factorResearch = {
  ...source.factorResearch,
  diagnostics: {
    ...source.factorResearch.diagnostics,
    features: undefined,
    dependence: { ...source.factorResearch.diagnostics?.dependence, jointDistributions: undefined },
  },
};
for (const key of ['trades', 'equity']) delete summary[key];
for (const key of ['ledger', 'decisions']) delete summary.execution[key];
delete summary.selection.trials;
summary.provenance = { ...summary.provenance, visualFixtureOnly: true };
const transport = {
  format: 'atlas.quant.bundle',
  version: 1,
  bundleId,
  logicalArtifactId: source.artifactId,
  complete: true,
  collections: Object.fromEntries(
    Object.entries(collections).filter(([, rows]) => Array.isArray(rows)).map(([key, rows]) => [key, { total: rows.length }])
  ),
  downloadUrl: '/quant/api/runs/visual-fixture/report/download',
};
const job = {
  id: 'visual-fixture',
  status: 'completed',
  name: '视觉预览 · 既有计算报告（接口模拟）',
  dataSource: report.provenance?.synthetic ? 'demo' : 'tushare',
  createdAt: new Date().toISOString(),
  strategy: report.strategy,
};
const related = (rows) => {
  const ids = new Set(rows.map((x) => x.targetId));
  const targets = (source.targetDefinitions || []).filter((x) => ids.has(x.id));
  const riskEvents = {};
  for (const row of rows) {
    riskEvents[row.date] = {
      riskExitCount:
        report.trades?.filter((x) => x.date === row.date && x.exitReason === 'risk_limit_exit')
          .length || 0,
      exitPendingCount:
        report.execution?.decisions?.filter(
          (x) => x.date === row.date && x.action === 'exit_pending'
        ).length || 0,
      breachCount: row.riskBreaches?.length || 0,
    };
  }
  return {
    targets,
    targetLabels: Object.fromEntries(targets.map((x) => [x.id, x.symbols.join(' / ')])),
    riskEvents,
  };
};
const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, 'http://localhost');
  const send = (data, headers = {}) => {
    response.writeHead(200, {
      'content-type': 'application/json',
      'cache-control': 'no-store',
      ...headers,
    });
    response.end(JSON.stringify(data));
  };
  try {
    if (request.method !== 'GET') {
      response.writeHead(405, { 'content-type': 'application/json' });
      response.end(JSON.stringify({ error: { message: 'visualFixtureOnly：只读预览不执行修改、拟合或 provider 请求。' } }));
      return;
    }
    if (url.pathname === '/quant/visual-model-functions.json') return send({ visualFixtureOnly: true, cases: functionFixtures.map(x => ({ name: x.name, artifact: x.artifact })) });
    if (url.pathname === '/quant/visual-model-functions') {
      response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      response.end(`<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>F 数值样本 · visualFixtureOnly</title><link rel="stylesheet" href="/quant/styles.css"><link rel="stylesheet" href="/quant/quant-workspace/workspace.css"><link rel="stylesheet" href="/quant/quant-workspace/report-model.css"></head><body><main style="max-width:1100px;width:100%;box-sizing:border-box;margin:auto;padding:16px"><h1>F 数值样本</h1><p>visualFixtureOnly · 已保存的数值黄金样本</p><label class="sq-field"><span>模型样本</span><select id="fixture-model"></select></label><section id="fixture-view"></section></main>${visualBanner}<script type="module">
        import { createForms } from '/quant/quant-workspace/forms.js';
        import { renderSavedModel } from '/quant/quant-workspace/report-model.js';
        const cases = (await (await fetch('/quant/visual-model-functions.json')).json()).cases;
        const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
        const C = { state: {}, esc, icon: () => '' }, F = createForms(C), picker = document.getElementById('fixture-model');
        picker.innerHTML = cases.map((x,n) => '<option value="'+n+'">'+esc(x.name)+'</option>').join(''); picker.value = '1';
        let output = 1, tree = 0;
        function render() { const sample = cases[Number(picker.value)]; document.getElementById('fixture-view').innerHTML = F.panel('保存的 F', renderSavedModel(C,F,{functionArtifact:sample.artifact,estimator:sample.name},{output,tree})); }
        document.addEventListener('change', event => { if(event.target.id === 'fixture-model'){output=1;tree=0;} if(event.target.id === 'sq-model-tree-output'){output=Number(event.target.value);tree=0;} if(event.target.id === 'sq-model-tree-index')tree=Number(event.target.value);render(); }); render();
      </script></body></html>`);
      return;
    }
    if (url.pathname.startsWith('/quant/api/')) {
      const endpoint = url.pathname.slice('/quant/api'.length);
      if (endpoint.endsWith('/report')) return send({ job, report: summary, transport });
      if (endpoint.endsWith('/report/download'))
        return send(
          { job, result: report },
          { 'content-disposition': 'attachment; filename="visual-fixture-only.json"' }
        );
      if (endpoint.endsWith('/report/chart'))
        return send({
          points: report.equity,
          totalPoints: report.equity.length,
          samplingMethod: 'fixture_all_points',
          range: [report.equity[0]?.date, report.equity.at(-1)?.date],
          bundleId,
        });
      if (endpoint.endsWith('/report/detail')) {
        const collection = url.searchParams.get('collection'),
          id = url.searchParams.get('id');
        const item = (collections[collection] || []).find(
          (x) => (x.forecastId || x.id || x.date) === id
        );
        return send({ item, related: related(item ? [item] : []), bundleId });
      }
      if (endpoint.endsWith('/report/pages')) {
        const p = url.searchParams,
          collection = p.get('collection');
        let rows = [...(collections[collection] || [])];
        if (collection === 'forecasts') {
          rows.sort((a, b) => b.date.localeCompare(a.date) || a.targetId.localeCompare(b.targetId));
          if (p.get('scope') === 'latest') {
            const groups = new Map();
            for (const row of rows) {
              const target = source.targetDefinitions.find((x) => x.id === row.targetId);
              const key = JSON.stringify([
                target?.symbols,
                target?.construction,
                target?.hedgeAudit?.projectionColumn,
              ]);
              if (!groups.has(key)) groups.set(key, row);
            }
            rows = [...groups.values()];
          }
        }
        if (p.get('status') === 'mature') rows = rows.filter(x => x.labelMaturedAt);
        else if (p.get('status') === 'unmatured') rows = rows.filter(x => !x.labelMaturedAt);
        else if (p.get('status')) rows = rows.filter((x) => x.status === p.get('status'));
        if (p.get('targetId')) rows = rows.filter((x) => x.targetId === p.get('targetId'));
        if (p.get('id')) rows = rows.filter((x) => (x.forecastId || x.id) === p.get('id'));
        if (p.get('dateFrom')) rows = rows.filter((x) => x.date >= p.get('dateFrom'));
        if (p.get('dateTo')) rows = rows.filter((x) => x.date <= p.get('dateTo'));
        if (collection === 'riskLedger' && p.get('filter') === 'events')
          rows = rows.filter((x) => {
            const events = related([x]).riskEvents[x.date];
            return events.riskExitCount || events.exitPendingCount || events.breachCount;
          });
        if (collection === 'riskLedger' && p.get('filter') === 'missing')
          rows = rows.filter(
            (x) =>
              x.unavailableRiskInputs?.factors?.length ||
              x.unavailableRiskInputs?.volatilitySymbols?.length
          );
        const offset = Number(p.get('offset') || 0),
          limit = Number(p.get('limit') || 25),
          items = rows.slice(offset, offset + Math.min(25, limit)).map(item => {
            if (!['modelFits','modelSearchCandidates'].includes(collection)) return item;
            const { functionArtifact, ...brief } = item;
            return brief;
          });
        return send({
          items,
          total: rows.length,
          offset,
          limit,
          nextOffset: offset + items.length,
          hasMore: offset + items.length < rows.length,
          related: related(items),
          bundleId,
        });
      }
      if (endpoint === '/session')
        return send({ visualFixtureOnly: true, capabilities: { tushareHosted: false }, runner: { online: false } });
      if (endpoint === '/catalog') return send(catalog);
      if (endpoint === '/runs') return send({ items: [job] });
      if (endpoint === '/statistical-quant/summary')
        return send({
          experiments: 0,
          forecastArtifacts: 1,
          completedExecutions: 1,
          comparisons: 0,
        });
      return send({ items: [], total: 0, fields: [], limits: {} });
    }
    const relative = url.pathname.replace(/^\/quant\/?/, '') || 'index.html';
    const asset = assets[relative];
    if (!asset) {
      response.writeHead(404);
      response.end();
      return;
    }
    const content = relative === 'index.html' ? asset.body.replace('</body>', visualBanner + '</body>') : asset.body;
    response.writeHead(200, {
      'content-type': asset.type,
      'cache-control': 'no-store',
    });
    response.end(asset.encoding === 'base64' ? Buffer.from(content, 'base64') : content);
  } catch (error) {
    response.writeHead(500, { 'content-type': 'application/json' });
    response.end(JSON.stringify({ error: { message: error.message } }));
  }
});
server.listen(Number(process.argv[3] || 8906), '127.0.0.1', () =>
  console.log(
    JSON.stringify({
      visualFixtureOnly: true,
      providerCalls: 0,
      url: `http://127.0.0.1:${server.address().port}/quant/#runs/visual-fixture`,
    })
  )
);
