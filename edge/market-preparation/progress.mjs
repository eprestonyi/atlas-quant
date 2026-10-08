/** A consistent, owner-scoped read of retained source receipts, not F progress. */
import { id, fail } from './common.mjs';
import { jobView } from './queue.mjs';

export async function readJobProgress(env, owner, jobId) {
  const row = await env.DB.prepare(`
    SELECT j.*,
      json_extract(p.spec,'$.budget.declaredRequests') declared_requests,
      (SELECT count(*) FROM quant_market_job_receipts r WHERE r.job_id=j.id) receipts_saved,
      (SELECT coalesce(sum(r.byte_length),0) FROM quant_market_job_receipts r WHERE r.job_id=j.id) raw_bytes_saved,
      (SELECT count(*) FROM quant_market_requests r WHERE r.job_id=j.id AND r.state='outcome_unknown') outcome_unknown
    FROM quant_market_jobs j
    JOIN quant_market_plans p ON p.id=j.plan_id AND p.owner=j.owner AND p.plan_root=j.plan_root
    WHERE j.id=? AND j.owner=?
  `).bind(id(jobId), owner).first();
  if (!row) fail('NOT_FOUND', '市场准备任务不存在', 404);
  const sourceProgress = {
    declaredRequests: row.declared_requests,
    receiptsSaved: row.receipts_saved,
    rawBytesSaved: row.raw_bytes_saved,
    outcomeUnknown: row.outcome_unknown,
  };
  if (!Object.values(sourceProgress).every(n => Number.isSafeInteger(n) && n >= 0) ||
      sourceProgress.declaredRequests === 0 ||
      sourceProgress.receiptsSaved > sourceProgress.declaredRequests ||
      sourceProgress.outcomeUnknown > sourceProgress.declaredRequests)
    fail('MARKET_PROGRESS_INTEGRITY', '来源回执计数与固定请求计划不一致', 409);
  return { ...jobView(row), sourceProgress };
}
