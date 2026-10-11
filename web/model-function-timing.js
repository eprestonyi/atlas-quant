/** F/5 time coordinates describe each input, never a mutually exclusive model mode. */
export function validateTimedReturnContext(a,input,{require:ok,keys,number}) {
  keys(input,['rows',...['originPrice','originVolatility','originDates','inputEvidence'].filter(key => Object.hasOwn(input,key))],'收益函数输入');
  ok(Array.isArray(input.rows) && input.rows.length <= 256,'单次函数试算最多256行');
  const normalized = a.featureConstruction.targetSpecification.normalization.kind === 'trailing_volatility';
  ok(normalized || input.originVolatility === undefined,'普通收益函数不接受波动率还原参数');
  ok(!normalized || input.originPrice === undefined || input.originVolatility !== undefined,'波动标准化收益还原价格需要起点已知波动率');
  const date = x => {
    ok(typeof x === 'string' && /^\d{8}$/.test(x) && x.slice(0,4) !== '0000','输入证据日期无效');
    const iso = `${x.slice(0,4)}-${x.slice(4,6)}-${x.slice(6,8)}`, parsed = new Date(iso+'T00:00:00Z');
    ok(Number.isFinite(parsed.valueOf()) && parsed.toISOString().slice(0,10) === iso,'输入证据日期不存在');
  };
  if (input.originDates !== undefined) {
    ok(Array.isArray(input.originDates) && input.originDates.length === input.rows.length,'起点日期需与输入行一一对应');
    input.originDates.forEach(date);
  }
  const names = a.inputSchema.map(x => x.name);
  const descriptors = new Map(a.featureConstruction.inputs.map(x => [x.feature,x]));
  input.rows.forEach(row => {
    keys(row,names,'输入行');
    for (const name of names) if (descriptors.get(name).timing.offsetSessions > 0)
      ok(number(row[name]),'未来因子需要明确数值，不能用训练中位数填补');
  });
  if (input.inputEvidence === undefined) return;
  ok(Array.isArray(input.inputEvidence) && input.inputEvidence.length === input.rows.length,'输入来源需与输入行一一对应');
  input.inputEvidence.forEach((row,index) => {
    keys(row,names,'逐因子来源');
    for (const name of names) {
      const evidence = row[name];
      keys(evidence,['valueOrigin','evaluationDate','availableAt'],'因子来源');
      ok(['observed_actual','scenario_assumption'].includes(evidence.valueOrigin),'因子来源类型无效');
      for (const key of ['evaluationDate','availableAt']) if (evidence[key] !== null) date(evidence[key]);
      if (evidence.valueOrigin === 'scenario_assumption') ok(number(input.rows[index][name]),'假设因子需要明确有限数值');
      if (evidence.valueOrigin === 'observed_actual' && evidence.availableAt !== null && evidence.evaluationDate !== null)
        ok(evidence.availableAt >= evidence.evaluationDate,'实测输入可用日不能早于观察日');
      const origin = input.originDates?.[index], evaluation = evidence.evaluationDate;
      if (origin && evaluation) {
        const offset = descriptors.get(name).timing.offsetSessions;
        ok(offset === 0 ? evaluation === origin : offset > 0 ? evaluation > origin : evaluation < origin,'因子观察日期与声明时点方向不一致');
      }
    }
  });
}

export function timedReturnEvidence(input) {
  const availabilityClasses = input.rows.map((_,index) => {
    const evidence = input.inputEvidence?.[index], origin = input.originDates?.[index];
    if (!evidence) return 'availability_unverified';
    const values = Object.values(evidence);
    if (values.some(x => x.valueOrigin === 'scenario_assumption')) return 'conditional_scenario';
    if (origin && values.some(x => x.availableAt !== null && x.availableAt > origin)) return 'conditional_realized';
    if (!origin || values.some(x => x.availableAt === null || x.evaluationDate === null)) return 'availability_unverified';
    return 'origin_known_declared';
  });
  return {availabilityClasses,inputEvidenceStatus:input.inputEvidence === undefined ? 'not_supplied' : 'caller_declared'};
}
