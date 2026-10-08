import { ApiError } from '../errors.mjs';
import { parsedStage } from './storage.mjs';

/** Remove only abandoned terminal uploads. Committed research is never TTL'd. */
export async function cleanupAbandonedBundles(env, cutoff) {
  const candidates = await env.DB.prepare(
    `SELECT s.id FROM quant_bundle_stages s JOIN jobs j ON j.id=s.job_id
    WHERE s.status IN ('staging','verified','aborted') AND j.status IN ('failed','cancelled')
      AND j.owner=s.owner AND j.lease_token=s.lease_token
      AND j.updated_at<? AND s.updated_at<?
      AND NOT EXISTS(SELECT 1 FROM quant_bundle_runs r WHERE r.stage_id=s.id)
      AND NOT EXISTS(SELECT 1 FROM quant_bundle_forecasts f WHERE f.stage_id=s.id OR f.dataset_stage_id=s.id)
    ORDER BY s.updated_at LIMIT 1`
  )
    .bind(cutoff, cutoff)
    .all();
  for (const candidate of candidates.results) {
    const stage = await env.DB.prepare(
      `UPDATE quant_bundle_stages SET status='aborted' WHERE id=? AND status IN ('staging','verified','aborted')
      AND EXISTS(SELECT 1 FROM jobs WHERE id=quant_bundle_stages.job_id
        AND owner=quant_bundle_stages.owner AND lease_token=quant_bundle_stages.lease_token
        AND status IN ('failed','cancelled') AND updated_at<?)
      AND NOT EXISTS(SELECT 1 FROM quant_bundle_runs r WHERE r.stage_id=quant_bundle_stages.id)
      AND NOT EXISTS(SELECT 1 FROM quant_bundle_forecasts f WHERE f.stage_id=quant_bundle_stages.id OR f.dataset_stage_id=quant_bundle_stages.id)
      RETURNING id,owner,manifest_key,manifest_text,bundle_id,metadata`
    )
      .bind(candidate.id, cutoff)
      .first();
    if (!stage) continue;
    const prefix = `bundle/${stage.owner}/${stage.id}`;
    if (
      !/^[A-Za-z0-9_-]{1,100}$/.test(stage.owner) ||
      !/^[A-Za-z0-9_-]{1,100}$/.test(stage.id) ||
      stage.manifest_key !== `${prefix}/manifest.json`
    ) {
      throw new ApiError('BUNDLE_INTEGRITY', '清理对象前缀与任务身份不一致', 503);
    }
    const parsed = await parsedStage(stage);
    // R2 put precedes the receipt transaction. A failed transaction can leave
    // an unreceipted object, so the immutable manifest is the cleanup authority.
    // Every key is reconstructed under this owner/stage, never accepted from a
    // receipt or user-provided path. The manifest permits at most 256 chunks.
    const keys = [stage.manifest_key];
    for (const collection of parsed.collections.values()) {
      for (const chunk of collection.chunks) {
        keys.push(`${prefix}/${collection.id}/${chunk.ordinal}-${chunk.sha256}.json`);
      }
    }
    // Multi-delete is idempotent after an interrupted attempt. Keep the aborted
    // stage and its manifest until all acknowledged and orphan writes are gone.
    await env.ARTIFACTS.delete(keys);
    await env.DB.batch([
      env.DB.prepare('DELETE FROM quant_bundle_records WHERE stage_id=?').bind(candidate.id),
      env.DB.prepare('DELETE FROM quant_bundle_chunks WHERE stage_id=?').bind(candidate.id),
      env.DB.prepare("DELETE FROM quant_bundle_stages WHERE id=? AND status='aborted'").bind(
        candidate.id
      )
    ]);
  }
  return candidates.results.length;
}
