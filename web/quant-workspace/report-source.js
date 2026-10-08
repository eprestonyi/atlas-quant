/** Bounded report pages. This cache never reconstructs an immutable artifact. */
export function createReportSource({ api, render }) {
  const cache = new Map();
  const queries = new Map();
  let transport = null;
  let runId = null;
  let generation = 0;
  let requestNumber = 0;
  const maxCachedPages = 12;

  const stableParams = (params) =>
    Object.fromEntries(
      Object.entries(params)
        .filter(([, value]) => value !== '' && value != null)
        .sort(([a], [b]) => a.localeCompare(b))
    );
  const identity = () => `${runId}:${transport?.bundleId}`;
  const enabled = () => transport?.format === 'atlas.quant.bundle' && transport.version === 1;
  const base = () => `/runs/${encodeURIComponent(runId)}/report`;

  function bind(next, nextRunId) {
    const nextIdentity = `${nextRunId}:${next?.bundleId}`;
    if (identity() !== nextIdentity) {
      generation++;
      cache.clear();
      queries.clear();
    }
    transport = next;
    runId = nextRunId;
  }
  function assertIdentity(response) {
    if (response.bundleId !== transport.bundleId)
      throw Error('报告分片身份不一致，请重新读取报告。');
  }
  function query(collection, filters = {}) {
    const params = stableParams({ collection, ...filters });
    const key = JSON.stringify(params);
    if (!queries.has(key)) queries.set(key, { key, params, offset: 0, previous: [] });
    return queries.get(key);
  }
  function retain(key, value) {
    cache.delete(key);
    cache.set(key, value);
    while (cache.size > maxCachedPages) cache.delete(cache.keys().next().value);
  }
  function start(entry, path) {
    const currentGeneration = generation;
    entry.loading = true;
    entry.error = '';
    entry.request = ++requestNumber;
    const request = entry.request;
    // Defer the network start so a synchronous render can finish first.
    Promise.resolve()
      .then(() => api(path))
      .then((response) => {
        if (generation !== currentGeneration || request !== entry.request) return;
        assertIdentity(response);
        if (entry.kind === 'page') {
          if (
            !Array.isArray(response.items) ||
            response.items.length > 100 ||
            !Number.isInteger(response.total) ||
            response.total < 0 ||
            response.offset !== entry.offset ||
            (response.hasMore &&
              (!Number.isInteger(response.nextOffset) || response.nextOffset <= entry.offset))
          )
            throw Error('报告分页返回格式无效，未将预览视为完整结果。');
        } else if (!Array.isArray(response.points) || response.points.length > 1000) {
          throw Error('净值曲线超过有界预览限制。');
        }
        entry.value = response;
        entry.loaded = true;
      })
      .catch((error) => {
        if (generation === currentGeneration && request === entry.request)
          entry.error = error.message;
      })
      .finally(() => {
        if (generation === currentGeneration && request === entry.request) {
          entry.loading = false;
          render();
        }
      });
  }
  function page(collection, filters = {}) {
    if (!Object.hasOwn(transport.collections || {}, collection)) {
      // Optional evidence is absent, not an empty calculated collection.
      return { unavailable: true, loading: false, loaded: false, items: [], related: {} };
    }
    const state = query(collection, filters);
    const key = `${state.key}:${state.offset}`;
    let entry = cache.get(key);
    if (!entry) {
      const params = new URLSearchParams({
        ...state.params,
        bundleId: transport.bundleId,
        offset: state.offset,
        limit: 25,
      });
      entry = {
        key,
        queryKey: state.key,
        kind: 'page',
        offset: state.offset,
        loading: true,
        loaded: false,
        error: '',
        value: null,
        path: `${base()}/pages?${params}`,
      };
      retain(key, entry);
      start(entry, entry.path);
    } else retain(key, entry);
    return {
      ...entry,
      items: entry.value?.items || [],
      total: entry.value?.total,
      hasMore: !!entry.value?.hasMore,
      previous: state.previous.length > 0,
      related: entry.value?.related || {},
    };
  }
  function chart() {
    const key = 'chart';
    let entry = cache.get(key);
    if (!entry) {
      entry = {
        key,
        kind: 'chart',
        loading: true,
        loaded: false,
        error: '',
        value: null,
        path: `${base()}/chart?${new URLSearchParams({ bundleId: transport.bundleId })}`,
      };
      retain(key, entry);
      start(entry, entry.path);
    }
    return entry;
  }
  function move(queryKey, direction) {
    const state = queries.get(queryKey);
    if (!state) return;
    const entry = cache.get(`${state.key}:${state.offset}`);
    if (!entry?.loaded || entry.loading || entry.error) return;
    if (direction === 'previous' && state.previous.length) state.offset = state.previous.pop();
    else if (direction === 'next' && entry.value.hasMore) {
      state.previous.push(state.offset);
      state.offset = entry.value.nextOffset;
    }
    render();
  }
  function retry(key) {
    const entry = cache.get(key);
    if (entry && !entry.loading) {
      start(entry, entry.path);
      render();
    }
  }
  async function detail(collection, id) {
    const currentGeneration = generation;
    const response = await api(
      `${base()}/detail?${new URLSearchParams({ collection, id, bundleId: transport.bundleId })}`
    );
    if (generation !== currentGeneration) return null;
    assertIdentity(response);
    return response;
  }
  return {
    bind,
    enabled,
    page,
    chart,
    move,
    retry,
    detail,
    get transport() {
      return transport;
    },
    diagnostics: () => ({
      cachedPages: cache.size,
      maxCachedPages,
      generation,
    }),
  };
}
