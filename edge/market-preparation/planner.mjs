/** No I/O to providers. Every possible request is declared before acquisition. */
import { PROFILE, canonical, digest, date, fail } from "./common.mjs";
const MiB = 1024 * 1024;
export const MARKET_FIELDS = Object.freeze([
  "open",
  "high",
  "low",
  "close",
  "raw_close",
  "vol",
  "amount",
  "adj_factor",
]);
export const DAILY_BASIC_FIELDS = Object.freeze(
  "turnover_rate turnover_rate_f volume_ratio pe pe_ttm pb ps ps_ttm dv_ratio dv_ttm total_share float_share free_share total_mv circ_mv".split(
    " ",
  ),
);
export const MARKET_LIMITS = Object.freeze({
  maxSymbols: 1000,
  maxCalendarDays: 366,
  maxRows: 300000,
  maxRequests: 3002,
  maxRawBytes: 512 * MiB,
  maxNormalizedBytes: 128 * MiB,
  maxRequestSeconds: 30,
  maxWallSeconds: 7200,
  leaseSeconds: 120,
  heartbeatSeconds: 20,
  requestsPerMinute: 60,
  maxActualAttemptsPerRequest: 1,
});
export function marketAuthorizationScope(env) {
  const value = env.MARKET_ACQUISITION_AUTH_SCOPE;
  return typeof value === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$/.test(value)
    ? value
    : null;
}
export async function createMarketPlan(
  scope,
  ref,
  input,
  authorizationScope,
  today,
) {
  if (input.profile !== PROFILE)
    fail("MARKET_PROFILE_UNSUPPORTED", "未注册的完整集合数据准备口径");
  if (
    !Array.isArray(input.requiredFields) ||
    input.requiredFields.length > 64 ||
    input.requiredFields.some((x) => typeof x !== "string") ||
    new Set(input.requiredFields).size !== input.requiredFields.length
  )
    fail("MARKET_FIELDS", "来源字段必须为不重复的注册字段");
  const fields = [
    ...new Set([...MARKET_FIELDS, ...input.requiredFields]),
  ].sort();
  const unknown = fields.filter(
    (x) => ![...MARKET_FIELDS, ...DAILY_BASIC_FIELDS].includes(x),
  );
  if (unknown.length)
    fail(
      "MARKET_FIELDS_UNSUPPORTED",
      "本市场准备口径不提供财报、事件或自有序列字段；不会伪造依赖",
    );
  const calendarDays = (date(scope.end) - date(scope.start)) / 86400000 + 1;
  const reasons = [];
  if (scope.symbolCount > MARKET_LIMITS.maxSymbols)
    reasons.push({
      code: "SYMBOL_BUDGET",
      message: "完整成员超过1000；本口径整单拒绝，不取前1000或50",
    });
  if (calendarDays > MARKET_LIMITS.maxCalendarDays)
    reasons.push({
      code: "DATE_BUDGET",
      message: "当前完整池口径最多366自然日；不会改变选择区间",
    });
  if (scope.end > today || scope.start < "20000101")
    reasons.push({
      code: "DATE_UNAVAILABLE",
      message: "日期须在2000年后且不晚于今天",
    });
  if (scope.symbols.some((x) => !/^\d{6}\.(SH|SZ)$/.test(x)))
    reasons.push({
      code: "VENUE_UNSUPPORTED",
      message: "当前计算口径尚未支持北京交易所；不会删除其中证券",
    });
  if (!authorizationScope)
    reasons.push({
      code: "PROVIDER_AUTHORIZATION_SCOPE_REQUIRED",
      message: "须配置独立且稳定的市场数据授权范围",
    });
  const basic = fields.some((x) => DAILY_BASIC_FIELDS.includes(x));
  const venues = [
    ...new Set(
      scope.symbols.map((x) => ({ SH: "SSE", SZ: "SZSE" })[x.slice(-2)]),
    ),
  ]
    .filter(Boolean)
    .sort();
  const requestCount = scope.symbolCount * (basic ? 3 : 2) + venues.length;
  if (requestCount > MARKET_LIMITS.maxRequests)
    reasons.push({
      code: "REQUEST_BUDGET",
      message: "完整请求计划超过口径预算",
    });
  // Even a blocked scope remains complete. Do not construct an oversized or
  // unsupported network plan merely to present the refusal.
  const requests = [];
  const actionable = !reasons.some((x) =>
    [
      "SYMBOL_BUDGET",
      "DATE_BUDGET",
      "DATE_UNAVAILABLE",
      "VENUE_UNSUPPORTED",
      "REQUEST_BUDGET",
    ].includes(x.code),
  );
  async function add(apiName, params, selectedFields, responseBytes) {
    const definition = {
      provider: "TUSHARE_PRO",
      authorizationScope,
      apiName,
      params,
      fields: selectedFields,
      responseBytes,
      maxAttempts: 1,
    };
    requests.push({
      ordinal: requests.length,
      requestKey: await digest(definition),
      ...definition,
    });
  }
  if (actionable) {
    for (const exchange of venues)
      await add(
        "trade_cal",
        { exchange, start_date: scope.start, end_date: scope.end },
        "exchange,cal_date,is_open,pretrade_date",
        64 * 1024,
      );
    for (const ts_code of scope.symbols) {
      const params = { ts_code, start_date: scope.start, end_date: scope.end };
      await add(
        "daily",
        params,
        "ts_code,trade_date,open,high,low,close,vol,amount",
        128 * 1024,
      );
      await add(
        "adj_factor",
        params,
        "ts_code,trade_date,adj_factor",
        64 * 1024,
      );
      if (basic)
        await add(
          "daily_basic",
          params,
          [
            "ts_code",
            "trade_date",
            ...fields.filter((x) => DAILY_BASIC_FIELDS.includes(x)),
          ].join(","),
          256 * 1024,
        );
    }
  }
  const rawCeiling = requests.reduce((sum, r) => sum + r.responseBytes, 0);
  if (rawCeiling > MARKET_LIMITS.maxRawBytes)
    reasons.push({
      code: "RAW_BYTE_BUDGET",
      message: "全部响应上界超过共同预算，拒绝整单",
    });
  const plan = {
    format: "atlas.quant.market_acquisition_plan",
    version: 1,
    profile: PROFILE,
    universeScopeRef: ref,
    membershipPolicy: "complete_filtered_set",
    scope: {
      symbols: scope.symbols,
      start: scope.start,
      end: scope.end,
      symbolCount: scope.symbolCount,
      scopeRoot: ref.scopeRoot,
    },
    catalog: {
      snapshotHash: scope.snapshotHash,
      resolutionHash: scope.resolutionHash,
      historicalMembershipVerified: false,
    },
    fields,
    authorizationScope,
    requests,
    blockedReasons: reasons,
    budget: {
      ...MARKET_LIMITS,
      calendarDays,
      declaredRequests: requestCount,
      materializedRequests: requests.length,
      rawResponseCeilingBytes: rawCeiling,
    },
    completeness: {
      zeroRowsForAnySymbol: "reject_entire_scope",
      missingSessions: "preserve_missing_mask",
      crossExchangeCalendars: "require_exact_session_equality",
      unknownRequest: "sticky_manual_review_no_retry",
    },
    sourcePolicy: {
      responseBytes: "exact_delivered_endpoint_bytes",
      originalProviderWireAvailable: false,
      adjustment: "adj_factor_divided_by_last_observed_factor_per_symbol",
      volumeUnit: "hands",
      amountUnit: "CNY_thousands",
    },
  };
  return { ...plan, planRoot: await digest(plan) };
}
export function planView(row, plan) {
  return {
    planRef: {
      planId: row.id,
      planRoot: row.plan_root,
      format: plan.format,
      version: 1,
    },
    universeScopeRef: plan.universeScopeRef,
    profile: plan.profile,
    scope: plan.scope,
    fields: plan.fields,
    budget: plan.budget,
    blockedReasons: plan.blockedReasons,
    canStart: false,
    status: plan.blockedReasons.length ? "blocked" : "planned",
    startUnavailableReason: "ISOLATED_MARKET_ACQUISITION_NOT_INSTALLED",
    requestsUrl: `/quant/api/market-preparation-plans/${row.id}/requests`,
    providerCalls: 0,
  };
}
