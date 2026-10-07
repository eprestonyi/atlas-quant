/** Local visual fixture server. NOT the Worker, storage, or performance acceptance path. */
import http from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { diagnosticSummary } from '../../edge/statistical-quant/summaries.mjs';

if (!process.argv[2])
  throw Error('Usage: node web/tests/report-preview.mjs /private/existing-report.json [port]');
const web = fileURLToPath(new URL('../', import.meta.url));
const input = JSON.parse(await fs.readFile(process.argv[2], 'utf8'));
const report = input.result || input;
const bundleId = 'b'.repeat(64);
const source = report.forecasts;
const collections = {
  forecasts: source.rows,
  targets: source.targetDefinitions,
  modelFits: source.modelFits,
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
    Object.entries(collections).map(([key, rows]) => [key, { total: rows?.length || 0 }])
  ),
  downloadUrl: '/quant/api/runs/visual-fixture/report/download',
};
const job = {
  id: 'visual-fixture',
  status: 'completed',
  name: '视觉预览 · 既有计算报告（接口模拟）',
  dataSource: report.provenance.synthetic ? 'demo' : 'tushare',
  createdAt: new Date().toISOString(),
  strategy: report.strategy,
};
const related = (rows) => {
  const ids = new Set(rows.map((x) => x.targetId));
  const targets = source.targetDefinitions.filter((x) => ids.has(x.id));
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
        if (p.get('status')) rows = rows.filter((x) => x.status === p.get('status'));
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
          items = rows.slice(offset, offset + limit);
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
        return send({ capabilities: { tushareHosted: true }, runner: { online: false } });
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
    const filename = path.resolve(web, relative);
    if (!filename.startsWith(web) || relative.startsWith('tests/')) {
      response.writeHead(404);
      response.end();
      return;
    }
    const content = await fs.readFile(filename);
    response.writeHead(200, {
      'content-type': filename.endsWith('.js')
        ? 'text/javascript'
        : filename.endsWith('.css')
          ? 'text/css'
          : 'text/html',
      'cache-control': 'no-store',
    });
    response.end(content);
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
