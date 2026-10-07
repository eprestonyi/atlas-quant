// Paged module discovery: catalog definitions describe capabilities, not verified research results.
import { describeFactor } from './defaults.js';
export function createModuleCatalog(C, F, onSelect) {
  const { esc: e, api, render, icon: i, toast } = C;
  const state = {
    items: [],
    total: 0,
    page: 1,
    pageSize: 12,
    q: '',
    stage: '',
    availability: 'all',
    counts: {},
    loading: false,
    error: '',
    request: 0,
  };
  const statusName = {
    ready: '可运行',
    needs_mapping: '需要映射',
    requires_data: '需要数据',
    schema_only: '定义可浏览',
    unavailable: '尚不可运行',
    planned: '规划中',
  };
  const portLabel = (value) =>
    ({
      market_panel: '行情与交易日历',
      price_target: '单资产价格目标',
      frozen_basket_target: '冻结数量篮子',
      causal_features_and_mature_labels: '状态与已成熟标签',
      conditional_price_forecast: '入场与未来状态预测',
      training_two_output_labels: '入场与未来成熟标签',
      causal_feature_panel: '当时可知的数值输入',
      fitted_features: '训练期处理后的特征',
      point_in_time_fields: '点时可用字段',
      numeric_feature: '数值状态输入',
      mature_forecasts: '已成熟预测',
      forecast_diagnostics: '预测误差与对照',
      forecast_artifact: '已冻结的预测产物',
      orders: '执行指令',
      forecast_orders: '引用预测的指令',
      bounded_target_positions: '风险约束后的仓位',
      cash_costs: '现金费用',
    })[value] || '按定义校验的研究产物';
  async function load(stage = state.stage) {
    state.stage = stage;
    state.loading = true;
    state.error = '';
    const request = ++state.request;
    render();
    try {
      const data = await api(
        '/statistical-quant/modules?' +
          new URLSearchParams({
            q: state.q,
            stage,
            availability: state.availability,
            page: state.page,
            pageSize: state.pageSize,
          })
      );
      if (request !== state.request) return;
      Object.assign(state, {
        items: data.items || [],
        total: data.total || 0,
        counts: data.counts || {},
      });
    } catch (error) {
      if (request === state.request) state.error = error.message;
    } finally {
      if (request === state.request) {
        state.loading = false;
        render();
      }
    }
  }
  function card(item) {
    const availability = item.availability?.status || 'unavailable';
    return `<article class="sq-module" ${availability === 'ready' ? `draggable="true" data-sq-drag="module:${e(item.id)}"` : ''}><div class="sq-module-meta"><span>${e(item.stage)} / v${e(item.version)}</span><span class="sq-status ${availability === 'ready' ? 'ready' : ''}">${e(statusName[availability] || availability)}</span></div><h3>${e(item.name)}</h3><p>${e(describeFactor(item.mechanism?.description || item.mechanism || item.description || item.availability?.reason || '查看模块定义与适用条件。'))}</p><div class="sq-module-types"><span>${e(portLabel(item.inputType))}</span>${i('arrow')}<span>${e(portLabel(item.outputType))}</span></div><div class="sq-module-bottom">${F.button('module-detail', '定义', { small: true, id: item.id })}${F.button('module-add', '选择', { icon: 'plus', small: true, id: item.id, disabled: availability !== 'ready' })}</div></article>`;
  }
  function view(stage) {
    return `<div class="sq-catalog"><div class="sq-drop-zone" data-sq-drop="${e(stage)}">${i('plus')}拖入本阶段模块应用配置，或使用“选择”按钮</div><div class="sq-catalog-tools"><label class="sq-search">${i('search')}<input id="sq-module-search" aria-label="搜索本阶段模块" placeholder="搜索方法、机制或数据要求" value="${e(state.q)}"></label><select id="sq-module-availability" aria-label="模块可用状态"><option value="all">全部状态</option><option value="ready" ${state.availability === 'ready' ? 'selected' : ''}>可运行</option><option value="unavailable" ${state.availability === 'unavailable' ? 'selected' : ''}>尚不可运行</option><option value="needs_mapping" ${state.availability === 'needs_mapping' ? 'selected' : ''}>需要映射</option></select></div><div class="sq-catalog-count">${state.total} 个原子模块 · 第 ${state.page} 页 · ${e(stage)}<span>模块定义不等于已验证策略</span></div>${state.error ? F.note(state.error, 'error') + F.button('module-retry', '重新加载', { small: true }) : ''}${state.loading ? '<div class="sq-loading" role="status"><span class="spinner"></span>读取模块目录</div>' : state.error ? '' : `<div class="sq-module-grid">${state.items.map(card).join('') || F.empty('尚无匹配模块', '调整搜索条件，或查看本阶段的明确配置。')}</div>`}<div class="sq-catalog-pagination"><span>每页 ${state.pageSize} 个</span><div>${F.button('module-page', '上一页', { small: true, page: state.page - 1, disabled: state.page <= 1 })}${F.button('module-page', '下一页', { small: true, page: state.page + 1, disabled: state.page * state.pageSize >= state.total })}</div></div></div>`;
  }
  async function handle(button) {
    const action = button.dataset.sq,
      item = state.items.find((x) => x.id === button.dataset.id);
    if (action === 'module-retry') {
      await load();
      return true;
    }
    if (action === 'module-page') {
      state.page = Number(button.dataset.page);
      await load();
      return true;
    }
    if (action === 'module-add') {
      if (!item || item.availability?.status !== 'ready') throw Error('这个模块尚不具备运行条件。');
      await onSelect(item);
      return true;
    }
    if (action === 'module-detail' && item) {
      C.openModal(
        item.name,
        `<p>${e(item.mechanism?.description || item.mechanism || item.description || '')}</p><pre class="validation-json">${e(JSON.stringify({ id: item.id, version: item.version, inputType: item.inputType, outputType: item.outputType, availability: item.availability, requiredData: item.requiredData, pointInTime: item.pointInTime, parameters: item.parameters, provenance: item.provenance }, null, 2))}</pre>`,
        true
      );
      return true;
    }
    return false;
  }
  let timer;
  function onInput(element) {
    if (element.id === 'sq-module-search') {
      state.q = element.value;
      state.page = 1;
      clearTimeout(timer);
      timer = setTimeout(() => load(), 250);
    }
  }
  function onChange(element) {
    if (element.id === 'sq-module-availability') {
      state.availability = element.value;
      state.page = 1;
      load().catch((error) => toast(error.message, true));
    }
  }
  return { state, load, view, handle, onInput, onChange };
}
