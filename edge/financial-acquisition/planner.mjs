import { ownerAllowed } from "./admission.mjs";
/** Pure, bounded planning. This module never opens a provider connection. */
import profile from "../../engine/atlas_quant/financial_acquisition/profile.json" with { type: "json" };
import definitions from "../financial/definitions.json" with { type: "json" };
import {
  object,
  string,
  id,
  date,
  fail,
  hashBytes,
  bytes,
} from "../financial/common.mjs";

export { profile };
export const CAPABILITY = "financial-acquire/v1";
export const canonical = (value) => JSON.stringify(sort(value));
function sort(value) {
  if (Array.isArray(value)) return value.map(sort);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sort(value[key])]),
    );
  return value;
}
export const digest = (value) => hashBytes(bytes(canonical(value)));
export function authorizationScope(env) {
  const scope = env.FINANCIAL_ACQUISITION_AUTH_SCOPE;
  return typeof scope === "string" &&
    /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,79}$/.test(scope)
    ? scope
    : null;
}
export function acquisitionEnabled(env, owner) {
  return (
    env.FINANCIAL_ACQUISITION_ENABLED === "true" &&
    env.TUSHARE_PUBLIC_AUTHORIZED === "true" &&
    !!authorizationScope(env) &&
    ownerAllowed(env, owner)
  );
}
export function requireAcquisition(env, owner) {
  if (!acquisitionEnabled(env, owner))
    fail(
      "FINANCIAL_ACQUISITION_UNAVAILABLE",
      "财务来源获取尚未开放或未获供应商授权",
      503,
    );
}
export function todayHongKong() {
  const fields = Object.fromEntries(
    new Intl.DateTimeFormat("en", {
      timeZone: "Asia/Hong_Kong",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    })
      .formatToParts(new Date())
      .map((part) => [part.type, part.value]),
  );
  return fields.year + fields.month + fields.day;
}
const epoch = (day) =>
  Date.UTC(
    Number(day.slice(0, 4)),
    Number(day.slice(4, 6)) - 1,
    Number(day.slice(6)),
  );

export async function createPlanSpec(value, scope, today = todayHongKong()) {
  object(value, [
    "requestId",
    "profile",
    "name",
    "symbols",
    "period",
    "start",
    "end",
    "announcementStart",
    "selectedStateIds",
  ]);
  id(value.requestId);
  const name = string(value.name, 80).trim();
  if (!name) fail("INVALID_INPUT", "请输入来源名称");
  if (value.profile !== profile.id)
    fail("ACQUISITION_PROFILE", "不支持的获取范围");
  if (!authorizationScope({ FINANCIAL_ACQUISITION_AUTH_SCOPE: scope }))
    fail("ACQUISITION_SCOPE", "未配置稳定的数据授权范围", 503);
  const symbols = value.symbols;
  if (
    !Array.isArray(symbols) ||
    symbols.length < 1 ||
    symbols.length > 2 ||
    symbols.some(
      (symbol) =>
        typeof symbol !== "string" || !/^\d{6}\.(SH|SZ)$/.test(symbol),
    ) ||
    new Set(symbols).size !== symbols.length
  )
    fail("ACQUISITION_SYMBOLS", "请选择一到两只唯一沪深股票");
  const venues = new Set(symbols.map((symbol) => symbol.slice(-2)));
  if (venues.size !== 1)
    fail(
      "MULTI_EXCHANGE_PROFILE_UNAVAILABLE",
      "当前获取范围要求同一交易所；不会自动替换跨市场日历",
    );
  for (const key of ["period", "start", "end", "announcementStart"])
    date(value[key]);
  date(today);
  if (
    !value.period.endsWith("1231") ||
    value.period < "20001231" ||
    value.period >= today
  )
    fail("ACQUISITION_PERIOD", "请选择已结束的十二月年报期间");
  if (
    !(
      value.period <= value.announcementStart &&
      value.announcementStart <= value.start &&
      value.start <= value.end &&
      value.end <= today
    ) ||
    (epoch(value.end) - epoch(value.announcementStart)) / 86400000 + 1 >
      profile.maxCalendarDays
  )
    fail(
      "ACQUISITION_DATES",
      "公告历史需覆盖研究区间，最多366日，且不得包含未来日期",
    );
  const selected = value.selectedStateIds;
  const supported = new Set(definitions.items.map((item) => item.id));
  if (
    !Array.isArray(selected) ||
    selected.length < 1 ||
    selected.length > 16 ||
    selected.some((item) => !supported.has(item)) ||
    new Set(selected).size !== selected.length
  )
    fail("ACQUISITION_STATES", "请明确选择一到十六个已登记财务定义");
  const selection = {
    symbols: [...symbols].sort(),
    period: value.period,
    start: value.start,
    end: value.end,
    announcementStart: value.announcementStart,
    selectedStateIds: [...selected].sort(),
    scope: profile.scope,
    flowBasis: profile.flowBasis,
    exchange: profile.exchanges[[...venues][0]],
  };
  const requests = [];
  async function request(endpoint, params) {
    const definition = {
      requestVersion: profile.requestVersion,
      profileVersion: `${profile.id}@${profile.version}`,
      provider: profile.provider,
      authorizationScope: scope,
      normalizerVersion: profile.normalizerVersion,
      endpoint,
      params,
      fields: [...profile.fields[endpoint]],
    };
    requests.push({ requestKey: await digest(definition), ...definition });
  }
  // Calendar is first so an invalid calendar stops subsequent provider work.
  await request("trade_cal", {
    exchange: selection.exchange,
    start_date: selection.announcementStart,
    end_date: selection.end,
  });
  for (const symbol of selection.symbols)
    for (const endpoint of profile.statementEndpoints)
      await request(endpoint, {
        ts_code: symbol,
        period: selection.period,
        report_type: profile.reportType,
        comp_type: profile.companyType,
      });
  const spec = {
    version: 1,
    profile: profile.id,
    name,
    selection,
    requests,
    budget: {
      maximumProviderCalls: requests.length,
      maximumResponseBytes: profile.maxResponseBytes,
      maximumTotalBytes: profile.maxTotalBytes,
    },
    limitations: [
      "GENERAL_COMPANY_CONSOLIDATED_ANNUAL_ONLY",
      "UNITS_UNVERIFIED",
      "ORIGINAL_AS_PUBLISHED_UNVERIFIED",
      "SINGLE_PERIOD_MAY_NOT_SUPPORT_TTM",
    ],
    researchBinding: false,
  };
  return { ...spec, planRoot: await digest(spec) };
}
