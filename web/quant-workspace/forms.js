// Shared accessible controls and guarded nested configuration access.
export function getPath(object, path) {
  return path.split('.').reduce((value, key) => value?.[key], object);
}
export function setPath(object, path, value) {
  const keys = path.split('.');
  if (keys.some((key) => ['__proto__', 'constructor', 'prototype'].includes(key)))
    throw Error('无效配置路径');
  let target = object;
  for (const key of keys.slice(0, -1)) target = target[key] ||= {};
  target[keys.at(-1)] = value;
}

export function createForms(C) {
  const { esc: e, state: s, icon: i } = C;
  const button = (
    action,
    label,
    {
      icon = '',
      primary = false,
      small = false,
      disabled = false,
      pressed,
      ariaLabel,
      ...attributes
    } = {}
  ) =>
    `<button class="sq-button ${primary ? 'primary' : ''} ${small ? 'small' : ''}" type="button" data-sq="${e(action)}" ${pressed !== undefined ? `aria-pressed="${Boolean(pressed)}"` : ''} ${ariaLabel ? `aria-label="${e(ariaLabel)}"` : ''} ${disabled ? 'disabled' : ''} ${Object.entries(
      attributes
    )
      .map(([key, value]) => `data-${e(key)}="${e(value)}"`)
      .join(' ')}>${icon ? i(icon) : ''}${e(label)}</button>`;
  const input = (
    label,
    path,
    { type = 'number', min, max, step = 1, help = '', value, unit = '' } = {}
  ) =>
    `<label class="sq-field"><span>${e(label)}${unit ? `<small>${e(unit)}</small>` : ''}</span><input id="sq-${e(path.replaceAll('.', '-'))}" data-sq-config="${e(path)}" type="${e(type)}" required value="${e(value ?? getPath(s.strategy, path) ?? '')}" ${min !== undefined ? `min="${min}"` : ''} ${max !== undefined ? `max="${max}"` : ''} ${type === 'number' ? `step="${step}"` : ''}>${help ? `<small>${e(help)}</small>` : ''}</label>`;
  const select = (label, path, options, help = '') =>
    `<label class="sq-field"><span>${e(label)}</span><select id="sq-${e(path.replaceAll('.', '-'))}" data-sq-config="${e(path)}">${Object.entries(
      options
    )
      .map(
        ([value, name]) =>
          `<option value="${e(value)}" ${String(getPath(s.strategy, path)) === value ? 'selected' : ''}>${e(name)}</option>`
      )
      .join('')}</select>${help ? `<small>${e(help)}</small>` : ''}</label>`;
  const toggle = (label, path, help = '') =>
    `<label class="sq-toggle"><input type="checkbox" data-sq-config="${e(path)}" ${getPath(s.strategy, path) ? 'checked' : ''}><span><strong>${e(label)}</strong>${help ? `<small>${e(help)}</small>` : ''}</span></label>`;
  const panel = (
    title,
    body,
    { kicker = '', description = '', actions = '', className = '' } = {}
  ) =>
    `<section class="sq-panel ${className}"><header>${kicker ? `<span class="sq-kicker">${e(kicker)}</span>` : ''}<div><h2>${e(title)}</h2>${actions}</div>${description ? `<p>${e(description)}</p>` : ''}</header><div class="sq-panel-body">${body}</div></section>`;
  const note = (text, kind = 'info') =>
    `<div class="sq-note ${kind}" ${kind === 'error' ? 'role="alert"' : ''}>${i(kind === 'error' ? 'info' : 'shield')}<p>${e(text)}</p></div>`;
  const empty = (title, description, actions = '') =>
    `<div class="sq-empty">${i('layers')}<h3>${e(title)}</h3><p>${e(description)}</p>${actions}</div>`;
  const advanced = (title, body, open = false) =>
    `<details class="sq-advanced" data-sq-details="${C.esc(title)}" ${open ? 'open' : ''}><summary>${e(title)}${i('sliders')}</summary><div>${body}</div></details>`;
  return { button, input, select, toggle, panel, note, empty, advanced };
}
