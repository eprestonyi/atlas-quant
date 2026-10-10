/** Single-asset return research. An asset collection never defines portfolio weights. */
import {returnFactorDescriptors} from '../../web/asset-return-factor-contract.js';
export const RETURN_STUDY_SCHEMA = 'asset-return-study/1';

export function normalizeReturnStudy(research, target, { keys, number, fail }) {
  const isReturn = target.kind === 'asset_return';
  if (!isReturn && (research.returnStudy !== undefined || target.normalization !== undefined))
    fail('收益研究协议只适用于逐资产收益目标');
  if (!isReturn) return null;
  const study = keys(research.returnStudy, ['schema', 'mode'], '收益研究协议');
  if (study.schema !== RETURN_STUDY_SCHEMA || !['forecast', 'association'].includes(study.mode))
    fail('收益研究模式无效');
  if (target.basket !== undefined) fail('研究集合不接受篮子权重');
  const scale = keys(target.normalization, ['kind', 'windowSessions', 'ddof', 'horizonScale', 'minimum'], '收益标准化');
  let normalization;
  if (scale.kind === 'none') {
    if (Object.keys(scale).length !== 1) fail('普通收益不接受波动率参数');
    normalization = { kind: 'none' };
  } else if (scale.kind === 'trailing_volatility') {
    if (scale.ddof !== 1 || scale.horizonScale !== 'sqrt_h' || scale.minimum !== 1e-8)
      fail('波动标准化须使用起点已知的历史样本波动率与明确期限尺度');
    normalization = { kind: scale.kind,
      windowSessions: number(scale.windowSessions, '波动率窗口', 20, 252, true),
      ddof: 1, horizonScale: 'sqrt_h', minimum: 1e-8 };
  } else fail('收益标准化类型无效');
  return { returnStudy: { schema: RETURN_STUDY_SCHEMA, mode: study.mode }, normalization };
}

export function assertReturnStudyScope(strategy, fail) {
  if (strategy.target.kind !== 'asset_return') return;
  if (strategy.model.parameterSharing !== 'per_target') fail('研究集合中的每只资产须独立拟合参数');
  if (strategy.execution.enabled !== false) fail('收益因子研究与交易执行独立');
  if (strategy.preprocess.automatic?.schema !== 'auto-factor-preprocess/2') fail('收益研究需要第二版类型化因子处理');
  if (!strategy.factors.length || strategy.factors.some(f => f.role === 'hedge')) fail('收益研究需要已选择的输入因子');
  if (strategy.model.family === 'pair_reversion') fail('收益集合研究不使用冻结两腿价格目标');
  try { returnFactorDescriptors(strategy); }
  catch (error) { fail(error.message); }
}
