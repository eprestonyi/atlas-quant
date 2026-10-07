/** Versioned composition catalog. Definitions and recipes are not alpha claims. */
import { ApiError } from '../errors.mjs';
import { json } from '../runtime.mjs';
import { validateExpression } from '../validation.mjs';

const ready = { status: 'ready', reason: '数值实现可用；实际数据覆盖和模型样本在运行时校验。' };
const definition = (id, name, stage, inputType, outputType, configPatch, extras = {}) => ({
  id,
  version: 1,
  name,
  stage,
  inputType,
  outputType,
  configPatch,
  patchSemantics: 'merge',
  availability: ready,
  capabilities: {
    implemented: true,
    requiresForecast: stage === 'execution',
    independentlyValidatedAlpha: false
  },
  requiredData: ['adjusted_ohlcv', 'official_calendar'],
  pointInTime: { required: true, policy: 'information_available_at_origin' },
  parameters: {},
  provenance: {
    owner: 'Atlas Quant',
    license: 'Apache-2.0',
    engineContract: 'statistical_quant/2'
  },
  ...extras
});
const parameter = (type, fallback, min, max) => ({
  type,
  default: fallback,
  ...(min === undefined ? {} : { min, max })
});
const families = {
  mean_reversion: ['条件均值回复', '过去状态可能约束指定期限的未来变化；估计系数可否定回复假设。'],
  pair_reversion: ['配对条件预测', '相关资产的冻结数量价差状态可能预测后续调整；不预设协整成立。'],
  trend: ['趋势条件预测', '已发生的价格变化与状态可能包含后续调整信息；参数只从成熟历史标签拟合。'],
  fundamental: ['基本面条件预测', '当时已公开的盈利、估值与财务变化可能解释指定期限价格变化。'],
  event: ['事件条件预测', '已知事件及已经发生的反应可能解释剩余调整；需要实际时点事件字段。']
};
const estimators = {
  no_change: '无变化基线',
  historical_drift: '历史平均变化',
  ridge: 'Ridge双目标回归',
  elastic_net: 'ElasticNet双目标回归',
  hist_gradient_boosting: '梯度提升双目标回归',
  auto: '有限候选时间验证'
};

export function moduleDefinitions(catalog) {
  const modules = [
    definition(
      'target.asset_price',
      '单资产价格',
      'target',
      'market_panel',
      'price_target',
      { target: { kind: 'asset_price', horizonSessions: 5 } },
      {
        parameters: { 'target.horizonSessions': parameter('integer', 5, 1, 60) },
        mechanism: '证券数量固定为1，同时预测未来入场和退出价格。'
      }
    ),
    ...['pair_ols', 'pca_residual', 'fixed'].map((method) =>
      definition(
        'target.' + method,
        { pair_ols: '配对OLS冻结篮子', pca_residual: 'PCA残差冻结篮子', fixed: '手动固定数量篮子' }[
          method
        ],
        'target',
        'market_panel',
        'frozen_basket_target',
        {
          target: {
            kind: 'frozen_basket',
            horizonSessions: 5,
            basket: { method, formationDays: 126 }
          }
        },
        {
          configurationRequired: true,
          parameters: {
            'target.basket.symbols': {
              type: 'symbol_array',
              required: true,
              minItems: method === 'pair_ols' ? 2 : method === 'pca_residual' ? 3 : 1,
              maxItems: method === 'pair_ols' ? 2 : 20
            },
            ...(method === 'pca_residual'
              ? { 'target.basket.components': parameter('integer', 2, 1, 10) }
              : {}),
            ...(method === 'fixed'
              ? { 'target.basket.quantities': { type: 'symbol_number_map', required: true } }
              : {})
          },
          mechanism:
            '每个预测原点冻结数量，同一数量向量用于当前状态、预测标签与交易。对冲构建本身不是未来预测。'
        }
      )
    ),
    ...Object.entries(families).map(([family, [name, mechanism]]) =>
      definition(
        'model.family.' + family,
        name,
        'model',
        'causal_features_and_mature_labels',
        'conditional_price_forecast',
        { model: { family } },
        {
          mechanism,
          compatibility:
            family === 'pair_reversion'
              ? { basketMethod: 'pair_ols' }
              : family === 'fundamental'
                ? { requires: 'observed_fundamental_predictor' }
                : family === 'event'
                  ? { requires: 'pit_event_role_factor' }
                  : {},
          requiredData:
            family === 'event'
              ? ['adjusted_ohlcv', 'official_calendar', 'observed_pit_events']
              : family === 'fundamental'
                ? ['adjusted_ohlcv', 'official_calendar', 'observed_pit_fundamentals']
                : ['adjusted_ohlcv', 'official_calendar']
        }
      )
    ),
    ...Object.entries(estimators).map(([estimator, name]) =>
      definition(
        'model.estimator.' + estimator,
        name,
        'model',
        'training_two_output_labels',
        'conditional_price_forecast',
        { model: { estimator } },
        {
          parameters: {
            'model.trainWindow': parameter('integer', 504, 120, 1260),
            'model.refitDays': parameter('integer', 20, 1, 126)
          },
          mechanism:
            estimator === 'no_change'
              ? '未来两个水平都等于已知当前状态；可产生完整预测，预期剩余变化为零。'
              : '共同拟合入场与退出的归一化水平变化，只有已经成熟的标签可以训练。'
        }
      )
    ),
    definition(
      'state.train_preprocessing',
      '训练期缩尾、缩放与去相关',
      'state',
      'causal_feature_panel',
      'fitted_features',
      {
        preprocess: {
          winsorize: true,
          standardize: true,
          decorrelation: 'drop_correlated',
          correlationThreshold: 0.9
        }
      },
      { parameters: { 'preprocess.correlationThreshold': parameter('number', 0.9, 0.5, 1) } }
    ),
    definition(
      'validation.nested_time',
      '内层选择、外层评估、顺序留出',
      'validation',
      'mature_forecasts',
      'forecast_diagnostics',
      { validation: { holdoutFraction: 0.2, minTrainDates: 80, innerFolds: 2, outerFolds: 2 } },
      {
        parameters: {
          'validation.holdoutFraction': parameter('number', 0.2, 0.1, 0.4),
          'validation.minTrainDates': parameter('integer', 80, 40, 252)
        },
        mechanism: '按日期切分并清除跨界标签；留出期仅按预声明日程用已成熟历史标签重新拟合。'
      }
    ),
    definition(
      'execution.forecast_only',
      '只研究预测',
      'execution',
      'forecast_artifact',
      'forecast_diagnostics',
      { execution: { enabled: false } }
    ),
    definition(
      'execution.expected_edge',
      '预测剩余变化与预计费用门控',
      'execution',
      'forecast_artifact',
      'orders',
      { execution: { enabled: true, minEdgeBps: 10, maxPositions: 5 } },
      {
        parameters: {
          'execution.minEdgeBps': parameter('number', 10, 0, 10000),
          'execution.maxPositions': parameter('integer', 5, 1, 50)
        },
        mechanism:
          '只消费有效forecastId，比较Vexit−Ventry与预计费用，不使用不可成交的当日收盘收益。'
      }
    ),
    definition(
      'risk.gross_and_weight',
      '总敞口与单股目标限额',
      'risk',
      'forecast_orders',
      'bounded_target_positions',
      { portfolio: { grossExposure: 1, maxWeight: 0.3 } },
      {
        parameters: {
          'portfolio.grossExposure': parameter('number', 1, 0.1, 2),
          'portfolio.maxWeight': parameter('number', 0.3, 0.01, 1)
        }
      }
    ),
    definition(
      'risk.net_exposure',
      '净敞口约束',
      'risk',
      'target_positions',
      'bounded_target_positions',
      { portfolio: { netExposureLimit: 0 } },
      {
        parameters: { 'portfolio.netExposureLimit': parameter('number', 0, 0, 2) },
        mechanism: '针对实际可成交数量检查净敞口；不以标签代替中性约束。'
      }
    ),
    definition(
      'risk.volatility_target',
      '过去波动估计与目标规模',
      'risk',
      'past_market_panel_and_targets',
      'scaled_target_positions',
      {
        portfolio: {
          sizingMode: 'volatility_target',
          targetAnnualVolatility: 0.1,
          volatilityLookback: 60
        }
      },
      {
        parameters: {
          'portfolio.targetAnnualVolatility': parameter('number', 0.1, 0.01, 1),
          'portfolio.volatilityLookback': parameter('integer', 60, 20, 252)
        }
      }
    ),
    definition(
      'risk.factor_exposure',
      '已选因子的暴露约束',
      'risk',
      'target_positions_and_selected_factors',
      'bounded_target_positions',
      { portfolio: { factorExposureLimits: [] } },
      {
        configurationRequired: true,
        parameters: {
          'portfolio.factorExposureLimits': {
            type: 'array',
            maxItems: 32,
            items: {
              factorId: { type: 'selected_factor_id' },
              maxAbsExposure: parameter('number', 1, 0, 5)
            }
          }
        }
      }
    ),
    definition(
      'cost.fixed_scenario',
      '佣金、滑点、税费与理论借券',
      'cost',
      'orders',
      'cash_costs',
      {
        costs: {
          commissionBps: 2.5,
          slippageBps: 3,
          sellTaxBps: 5,
          transferBps: 0.1,
          minCommission: 5,
          borrowAnnualBps: 300
        }
      },
      { mechanism: '固定可编辑费用情景，不代表券商报价或历史税费复刻。' }
    )
  ];
  for (const factor of catalog.factors) {
    const fields = factor.requiredFields ?? validateExpression(factor.expression).fields;
    modules.push(
      definition(
        'state.factor.' + factor.id,
        factor.name,
        'state',
        'point_in_time_fields',
        'numeric_feature',
        {
          factors: [
            {
              id: factor.id,
              expression: factor.expression,
              direction: factor.direction === -1 ? -1 : 1,
              role: 'predictor',
              version: 1
            }
          ]
        },
        {
          patchSemantics: 'append_factors',
          family: factor.family,
          category: factor.category,
          description: factor.description,
          mechanism: '提供信息时点可知的预测输入；配方本身不产生交易或已验证alpha。',
          requiredData: fields,
          pointInTime: {
            required: true,
            policy: fields.some((f) => /^(fd|pcd|ext|model)_/.test(f))
              ? 'explicit_available_date_asof'
              : 'past_market_observations'
          },
          expression: factor.expression,
          lookback: factor.lookback
        }
      )
    );
  }
  const eventFields = [
    ...new Set(
      catalog.factors
        .flatMap((f) => f.requiredFields ?? validateExpression(f.expression).fields)
        .filter((f) => /^(fd|pcd|ext)_/.test(f))
    )
  ];
  for (const field of eventFields)
    modules.push(
      definition(
        'state.event.' + field,
        '已公告变化 · ' + field,
        'state',
        'point_in_time_field',
        'event_feature',
        {
          factors: [
            {
              id: 'event_' + field,
              expression: `delta(${field},1)`,
              direction: 1,
              role: 'event',
              version: 1
            }
          ]
        },
        {
          patchSemantics: 'append_factors',
          requiredData: [field],
          mechanism:
            '只在当时可知数值变化时产生事件输入；样本不足明确失败，不把每日财务水平伪装成新公告。'
        }
      )
    );
  return modules;
}

/** Finite, inspectable configurations, not a parameter-product alpha counter. */
export function recipeDefinitions(catalog, packs) {
  const recipes = [];
  const packOptions = [{ id: 'mechanism_state_only', name: '模型自带状态', factors: [] }, ...packs];
  for (const [family, [familyName]] of Object.entries(families))
    for (const pack of packOptions) {
      const fields = pack.factors.flatMap((f) => validateExpression(f.expression).fields);
      const fundamental = fields.some(
        (f) =>
          /^(fd|pcd)_/.test(f) ||
          ['pe', 'pe_ttm', 'pb', 'ps', 'ps_ttm', 'dv_ttm', 'total_mv', 'circ_mv'].includes(f)
      );
      if (family === 'fundamental' && !fundamental) continue;
      if (family === 'event' && !fields.some((f) => /^(fd|pcd|ext)_/.test(f))) continue;
      const targets =
        family === 'pair_reversion' ? ['pair_ols'] : ['asset_price', 'pair_ols', 'pca_residual'];
      for (const target of targets)
        for (const estimator of [
          'no_change',
          'historical_drift',
          'ridge',
          'elastic_net',
          'hist_gradient_boosting',
          'auto'
        ]) {
          const factors =
            family === 'event'
              ? [...new Set(fields.filter((f) => /^(fd|pcd|ext)_/.test(f)))].map((field, i) => ({
                  id: 'event_' + field,
                  expression: `delta(${field},1)`,
                  direction: 1,
                  version: 1,
                  role: 'event'
                }))
              : pack.factors.map((f) => ({
                  id: f.id,
                  expression: f.expression,
                  direction: f.direction === -1 ? -1 : 1,
                  version: 1,
                  role: 'predictor'
                }));
          const targetPatch =
            target === 'asset_price'
              ? { kind: 'asset_price', horizonSessions: 5 }
              : {
                  kind: 'frozen_basket',
                  horizonSessions: 5,
                  basket: { method: target, formationDays: 126 }
                };
          const id = [family, target, estimator, pack.id].join('__');
          recipes.push({
            id,
            version: 1,
            name: `${familyName} · ${pack.name} · ${estimators[estimator]} · ${target}`,
            description: '版本化模块配置配方；尚需用户选择标的、真实数据及篮子成员。',
            moduleIds: [
              'target.' + target,
              'model.family.' + family,
              'model.estimator.' + estimator,
              ...(family === 'event'
                ? factors.map((f) => 'state.event.' + f.id.slice(6))
                : pack.factors.map((f) => 'state.factor.' + f.id)),
              'validation.nested_time',
              'execution.expected_edge',
              'risk.gross_and_weight',
              'cost.fixed_scenario'
            ],
            configPatch: {
              target: targetPatch,
              model: { family, estimator, trainWindow: 504, refitDays: 20 },
              factors
            },
            patchSemantics: 'merge_replace_factors',
            availability: ready,
            configurationRequired: true,
            requiredData: [...new Set(fields)],
            pointInTime: { required: true },
            countsAs: 'configuration_recipe',
            runCount: null,
            runCountStatus: 'not_inferred_from_definition',
            validatedAlpha: false
          });
        }
    }
  return recipes;
}

export function statisticalCatalog(req, path, catalog, presets) {
  if (
    req.method !== 'GET' ||
    ![
      '/statistical-quant/modules',
      '/statistical-quant/recipes',
      '/statistical-quant/workspaces'
    ].includes(path)
  )
    return null;
  if (path.endsWith('/workspaces'))
    return json({
      items: [
        {
          id: 'statistical_quant',
          name: '统计量化交易',
          implemented: true,
          mode: 'statistical_quant',
          schemaVersion: 2
        },
        {
          id: 'market_making',
          name: '做市',
          implemented: false,
          reason: '独立订单簿与库存模型尚未实现'
        },
        {
          id: 'derivatives',
          name: '波动率与衍生品',
          implemented: false,
          reason: '独立定价、希腊值与合约执行尚未实现'
        },
        {
          id: 'structural',
          name: '复制关系与结构交易',
          implemented: false,
          reason: '独立合约关系及可交易约束尚未实现'
        }
      ]
    });
  const p = new URL(req.url).searchParams,
    q = (p.get('q') ?? '').trim().toLowerCase().slice(0, 100),
    stage = p.get('stage') ?? 'all',
    availability = p.get('availability') ?? 'all';
  const page = Number(p.get('page') ?? 1),
    pageSize = Number(p.get('pageSize') ?? 30);
  if (
    !Number.isInteger(page) ||
    page < 1 ||
    page > 10000 ||
    !Number.isInteger(pageSize) ||
    pageSize < 1 ||
    pageSize > 100 ||
    !['all', 'ready', 'needs_mapping', 'unavailable'].includes(availability)
  )
    throw new ApiError('INVALID_PAGE', '模块分页或可用性筛选无效');
  const modules = moduleDefinitions(catalog),
    recipes = recipeDefinitions(catalog, presets.packs ?? []),
    isRecipe = path.endsWith('/recipes');
  if (
    ![
      'all',
      'target',
      'state',
      'model',
      'validation',
      'hedge',
      'risk',
      'execution',
      'cost'
    ].includes(stage)
  )
    throw new ApiError('INVALID_STAGE', '模块阶段不存在');
  const all = isRecipe ? recipes : modules;
  const matching = all.filter(
    (item) =>
      (stage === 'all' || item.stage === stage) &&
      (availability === 'all' || item.availability.status === availability) &&
      (!q ||
        [
          item.id,
          item.name,
          item.description,
          item.family,
          item.category,
          item.expression,
          item.mechanism
        ]
          .join(' ')
          .toLowerCase()
          .includes(q))
  );
  return json({
    schemaVersion: 1,
    items: matching.slice((page - 1) * pageSize, page * pageSize),
    total: matching.length,
    page,
    pageSize,
    counts: {
      atomicModules: modules.length,
      configurationRecipes: recipes.length,
      validatedAlpha: 0,
      experimentCountScope: 'not_inferred_from_catalog'
    },
    definitionsAreNotExperiments: true,
    limits: {
      maxSymbols: 50,
      maxFactors: 32,
      maxDataRows: 110000,
      maxForecastRows: 25000,
      maxArtifactBytes: 24 * 1024 * 1024,
      maxHorizonSessions: 60
    }
  });
}
