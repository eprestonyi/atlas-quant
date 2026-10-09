// Product hierarchy. Only completed capabilities receive runnable actions.
export function createModuleHub(C, F, { heading, route }) {
  const { esc: e, icon: i, state: s } = C;
  const modules = [
    ['modes', '因子研究', '构建 F(X)，检验预测误差与因子信息，保存独立的模型版本。', '进入因子研究', 'model'],
    ['strategies', '策略研究', '未来从因子模型导入预测，研究指数增强、绝对收益、市场 beta、方向与对冲。', '查看开发范围', 'layers'],
    ['instruments', '策略执行仪器', '未来连接冻结策略、阈值与真实交易事件；中低频和高频分别接入。', '查看开发范围', 'workflow'],
    ['monitor', 'E 检测仪', '独立选择 Atlas 自选股与因子模型，检测 P − F(X)。监测名单不改变训练票池。', '查看接入范围', 'chart'],
  ];
  function modes() {
    return heading('统计量化交易 / 因子研究', '选择模式', '') +
      `<div class="sq-mode-grid">${[
        ['easy', '轻松模式', '选择机制和研究范围，系统完成估计器比较与因子拟合。', 'workflow'],
        ['studio', 'Quant Studio', '展开数据、因子定义、验证参数和代码，逐项控制研究协议。', 'code'],
      ].map(([id, title, description, icon]) => `<a class="sq-mode-card" data-sq="select-mode" data-id="${id}" href="#quant/${id}/universe"><span>${i(icon)}</span><h2>${title}</h2><p>${description}</p><strong>进入 ${i('arrow')}</strong></a>`).join('')}</div>`;
  }
  function statistical() {
    return heading('ATLAS QUANT', '统计量化交易', '') +
      `<div class="sq-module-hub">${modules.map(([id, title, description, action, icon], n) => `<article class="sq-module-card ${n ? 'planned' : 'available'}"><span class="sq-kicker">0${n + 1} · ${n ? '建设中' : '因子工作台'}</span><span class="sq-module-icon">${i(icon)}</span><h2>${title}</h2><p>${description}</p><a class="sq-button ${n ? '' : 'primary'}" href="#${id === 'modes' ? 'quant/modes' : route(id)}">${action}${i('arrow')}</a></article>`).join('')}</div>`;
  }
  function pending(id) {
    const item = modules.find(x => x[0] === id);
    const scope = {
      strategies: ['导入版本化因子模型', '定义指数增强、绝对收益或市场 beta 目标', '配置 long only / 多空、自动对冲与交易阈值', '独立回测策略与成本'],
      instruments: ['中低频执行：连接已冻结策略和阈值', '真实订单、成交与风险事件台账', '高频：需要交易所数据、低延迟链路与执行接口'],
      monitor: ['选择独立 Atlas 自选股名单', '绑定一个已验证来源的因子模型版本', '以模型支持的频率计算 P、V 与 E', '记录输入过期、范围外标的和模型不适用情况'],
    }[id];
    return heading('STATISTICAL QUANT / ' + id.toUpperCase(), item[1], item[2]) +
      F.panel('开发范围', F.note(id === 'monitor' ? '尚未连接实时数据与模型推断，当前不展示实时 E。' : '此模块尚未开放运行。当前可完成因子研究，不生成模拟交易流。') +
        `<ol class="sq-module-roadmap">${scope.map(x => `<li>${e(x)}</li>`).join('')}</ol><a class="sq-button primary" href="#${'quant/modes'}">进入因子研究 ${i('arrow')}</a>`);
  }
  return { modes, statistical, pending };
}
