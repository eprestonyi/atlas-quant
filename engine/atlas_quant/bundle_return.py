"""Strict study/2 archive records. Older return protocols do not call these gates."""
import math
from .factors import validate_expression
from .context_sources import context_field
from .statistical_quant.return_study.contract import input_descriptors

COORDINATE_KEYS = {'inputEvidence', 'inputAvailableAt', 'sampleReadyAt', 'availabilityClass'}
EVIDENCE_KEYS = {'valueOrigin', 'evaluationDate', 'availableAt', 'sourceDate', 'availabilityBasis', 'knownAtOrigin', 'status'}


def evidence_contract(strategy, dates):
    inputs = input_descriptors(strategy)
    for item in inputs:
        fields = validate_expression(item['expression'])['fields']
        unknown = any(f.startswith(('fd_', 'model_fin_')) or f.startswith('ext_') and context_field(f) is None for f in fields)
        item['basis'] = 'declared_data_alignment' if unknown else 'native_source_session_policy' if item['clock'] == 'observed_source_sessions_asof' else 'research_session_policy'
    return dates, inputs, strategy['target']['horizonSessions']


def verify_coordinate_row(row, contract, fail):
    def check(ok, message):
        if not ok:
            fail('BUNDLE_RETURN_EVIDENCE', message)
    dates, inputs, h = contract
    check(row['date'] in dates, '时移研究观察不在冻结日历中。')
    t, evidence = dates.index(row['date']), row.get('inputEvidence')
    end = dates[t+h] if t+h < len(dates) else None
    check(row.get('responseStartDate') == row['date'] and row.get('responseEndDate') == end, '目标区间与冻结日历不一致。')
    check(isinstance(evidence, dict) and set(evidence) == {x['feature'] for x in inputs}, '逐输入证据与因子实例不一致。')
    unresolved, conditional, unknown, available = False, False, False, []
    for item in inputs:
        e, j = evidence[item['feature']], t+item['timing']['offsetSessions']
        evaluation = dates[j] if 0 <= j < len(dates) else None
        check(isinstance(e, dict) and set(e) == EVIDENCE_KEYS, '逐输入证据字段不一致。')
        check(e['valueOrigin'] == 'observed_actual' and e['evaluationDate'] == evaluation and e['availableAt'] == evaluation and e['availabilityBasis'] == item['basis'], '时移或可用日政策不一致。')
        known = None if evaluation is None or item['basis'] == 'declared_data_alignment' else evaluation <= row['date']
        check(e['knownAtOrigin'] is known, '条件或未验证输入被错误声明为起点已知。')
        if evaluation is None:
            check(e['status'] == 'outside_available_calendar' and e['sourceDate'] is None, '越界输入被伪造为已观测。')
            unresolved = True
        else:
            check(e['status'] in {'observed', 'missing_observation'}, '输入观测状态无效。')
            if item['basis'] == 'declared_data_alignment':
                check(e['sourceDate'] is None, '未验证数据对齐不能伪造来源观察日期。')
            elif item['clock'] == 'observed_source_sessions_asof':
                check(e['sourceDate'] is None or isinstance(e['sourceDate'], str) and e['sourceDate'] < evaluation, '来源日期越过对齐边界。')
                check(e['sourceDate'] is not None or e['status'] == 'missing_observation', '不存在的来源被声明已观测。')
            else:
                check(e['sourceDate'] == evaluation, '格点来源日期与时移不一致。')
            unresolved |= item['timing']['offsetSessions'] > 0 and e['status'] == 'missing_observation'
        if 'features' in row:
            value = row['features'][item['feature']]
            check((e['status'] == 'observed') == (type(value) in (int, float) and math.isfinite(value)), '数值与观测证据不一致。')
        available.append(e['availableAt'])
        conditional |= e['availableAt'] is not None and e['availableAt'] > row['date']
        unknown |= e['knownAtOrigin'] is None
    latest = max(available) if available and all(x is not None for x in available) else None
    ready = max(latest, end) if latest is not None and end is not None else None
    usage = 'unresolved_input' if unresolved else 'conditional_realized' if conditional else 'availability_unverified' if unknown else 'origin_known_by_policy'
    check(row.get('inputAvailableAt') == latest and row.get('sampleReadyAt') == ready and row.get('availabilityClass') == usage, '样本成熟时点或条件分类不一致。')
    check(not unresolved or row['inputValid'] is False and row.get('status') != 'valid', '未解决的输入不能作为有效样本。')
    if row['schema'] == 'asset-return-observation/2':
        check(row.get('informationCutoff') == (max(latest,row['date'])+'_AFTER_CLOSE' if latest else None), '观察信息截止与输入及拟合起点不一致。')


def verify_coordinate_fit(fit, strategy, fail):
    if fit.get('status') != 'valid':
        return
    from .statistical_quant.model_function import validate_function
    artifact = fit.get('functionArtifact')
    if not isinstance(artifact, dict) or artifact.get('schema') != 'atlas-model-function/5':
        fail('BUNDLE_RETURN_FUNCTION', '时移研究拟合缺少第五版函数。')
    validate_function(artifact)
    if (artifact.get('lineage') != {'status': 'fitted', 'parentArtifactId': None}
            or artifact['scope']['symbols'] != [fit.get('targetSymbol')]
            or artifact['featureConstruction']['factors'] != strategy['factors']
            or artifact['featureConstruction']['targetSpecification'] != strategy['target']
            or artifact['featureConstruction']['preprocess'] != strategy['preprocess']
            or artifact['featureConstruction']['inputs'] != fit.get('returnInputDescriptors')
            or any(value != fit.get(key) for key, value in artifact['training'].items())
            or artifact['training']['informationCutoff'] != fit.get('fitDate')):
        fail('BUNDLE_RETURN_FUNCTION', '第五版函数与冻结拟合或研究声明不一致。')
