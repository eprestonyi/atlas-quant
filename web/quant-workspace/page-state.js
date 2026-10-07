/** Preserve interaction state across complete DOM renders without mutating research data. */
export function createPageState(root, win = window) {
  const pages = new Map();
  let renderedRoute = null,
    pending = null;
  const interactive = 'button,input,select,textarea,a[href],summary,[tabindex]';
  const detailKey = (node) =>
    node.id || node.dataset.sqDetails || node.querySelector('summary')?.textContent;
  function signature(node) {
    if (node.id) return `id:${node.id}`;
    if (node.tagName === 'SUMMARY') return `summary:${detailKey(node.parentElement)}`;
    const data = Object.entries(node.dataset || {}).sort(([a], [b]) => a.localeCompare(b));
    return JSON.stringify([
      node.tagName,
      node.getAttribute('type'),
      node.getAttribute('name'),
      node.getAttribute('href'),
      data,
      node.getAttribute('aria-label'),
    ]);
  }
  function focusReference() {
    const node = win.document.activeElement;
    if (!root.contains(node) || !node.matches(interactive)) return null;
    const key = signature(node),
      peers = [...root.querySelectorAll(interactive)].filter((x) => signature(x) === key);
    return {
      key,
      index: peers.indexOf(node),
      selection:
        typeof node.selectionStart === 'number' ? [node.selectionStart, node.selectionEnd] : null,
    };
  }
  function snapshot() {
    return {
      scroll: [win.scrollX || 0, win.scrollY || 0],
      details: new Map(
        [...root.querySelectorAll('details')]
          .filter((x) => x.id !== 'sq-summary')
          .map((x) => [detailKey(x), x.open])
      ),
      focus: focusReference(),
    };
  }
  function before(route) {
    if (renderedRoute !== null) pages.set(renderedRoute, snapshot());
    pending = { route, changed: renderedRoute !== route, saved: pages.get(route) };
  }
  function after() {
    if (!pending) return;
    const { route, changed, saved } = pending;
    pending = null;
    renderedRoute = route;
    const heading = root.querySelector('h1') || root.querySelector('main');
    if (heading) heading.tabIndex = -1;
    root.querySelectorAll('details').forEach((node) => {
      const key = detailKey(node);
      if (saved?.details.has(key)) node.open = saved.details.get(key);
    });
    if (changed) {
      heading?.focus({ preventScroll: true });
      win.scrollTo(...(saved?.scroll || [0, 0]));
    } else if (saved?.focus) {
      const target = [...root.querySelectorAll(interactive)].filter(
        (x) => signature(x) === saved.focus.key
      )[saved.focus.index];
      if (target && !target.disabled) {
        target.focus({ preventScroll: true });
        if (saved.focus.selection)
          try {
            target.setSelectionRange(...saved.focus.selection);
          } catch {}
      } else heading?.focus({ preventScroll: true });
    }
    win.document.title = `${heading?.textContent?.trim() || '开放量化研究'} · Atlas Quant`;
  }
  function focusMain() {
    const main = root.querySelector('main');
    if (main) {
      main.tabIndex = -1;
      main.focus();
      main.scrollIntoView?.({ block: 'start' });
    }
  }
  return { before, after, focusMain };
}
