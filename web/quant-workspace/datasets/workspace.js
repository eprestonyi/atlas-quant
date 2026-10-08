import { createDatasetComposer } from './composer.js';

// A private owner and a protocol each own their form and idempotency keys.
// Switching views cannot reuse a legacy plan or accept another owner's response.
export function createDatasetWorkspace(C, F, options) {
  const instances = new Map();
  function active() {
    const owner = C.state.session?.workspace?.id || null;
    const graph = location.hash.split('/')[3] === 'graph';
    const key = JSON.stringify([owner, graph]);
    if (!instances.has(key)) instances.set(key, createDatasetComposer(C, F, { ...options, owner, graph }));
    return instances.get(key);
  }
  return {
    render: () => active().render(),
    routeChanged(force = false) {
      const selected = active();
      for (const instance of instances.values()) if (instance !== selected) instance.dispose();
      return selected.routeChanged(force);
    },
    get state() { return active().state; },
    dispose() { for (const instance of instances.values()) instance.dispose(); },
  };
}
