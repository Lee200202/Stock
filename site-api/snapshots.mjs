/* 公開快照：一輪一個 KV 值，逐項核對時間不隨心跳續命。
   協調器使用 SQLite Durable Object 的小型計數器，不用 KV 寫計數器。 */
export const BUNDLE_KEY = 'snapshot:bundle:v2';
export const SNAP_FRESH_MS = 12 * 60_000;
export const WRITE_SLOT_MS = 5 * 60_000;
export const DAILY_WRITE_LIMIT = 450;
export const SNAPPED = new Set(['apiGetDashboard', 'apiListMailDates', 'apiGetMailContent',
  'apiListRecordDates', 'apiGetHoldingsTracker', 'apiGetPerformanceSeries']);
const bundleMemo = new WeakMap();

function validBundle(v) {
  return v?.version === 2 && v.entries && typeof v.entries === 'object' && !Array.isArray(v.entries);
}
function usable(payload) {
  if (!payload?.ok || payload.result == null) return false;
  const r = payload.result;
  return Array.isArray(r) ? r.length > 0 : typeof r === 'object' ? Object.keys(r).length > 0 : true;
}
async function digest(method, payload) {
  const content = method === 'apiGetDashboard' && payload?.result
    ? {...payload, result: {...payload.result, updatedAt: undefined}} : payload;
  const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(content)));
  return Array.from(new Uint8Array(bytes).slice(0, 12), b => b.toString(16).padStart(2, '0')).join('');
}

export async function loadBundle(kv, fresh = false) {
  if (!kv) return null;
  // 同一執行個體短期共用整包，並合併同時到達的不同方法讀取；跨執行個體仍由 KV/邊緣快取供應。
  const hit = bundleMemo.get(kv);
  if (!fresh && hit?.pending) return hit.pending;
  if (!fresh && hit && Date.now() - hit.at < (hit.value ? 15_000 : 5_000)) return hit.value;
  const entry = {at: 0, value: null, pending: null};
  entry.pending = kv.get(BUNDLE_KEY, 'json').then(value => {
    entry.value = validBundle(value) ? value : null;
    entry.at = Date.now();
    return entry.value;
  }).catch(() => { entry.at = Date.now(); return null; }).finally(() => { entry.pending = null; });
  bundleMemo.set(kv, entry);
  return entry.pending;
}

export async function readSnapshot(env, key) {
  if (!SNAPPED.has(key.split(':')[0])) return null;
  const bundle = await loadBundle(env.SNAP);
  const entry = bundle?.entries[key];
  const now = Date.now();
  if (!entry || !usable(entry.payload) || !Number.isFinite(entry.at) || !Number.isFinite(entry.validatedAt) ||
      entry.validatedAt > now + 60_000 || now - entry.validatedAt >= SNAP_FRESH_MS) return null;
  return {payload: entry.payload, at: entry.at, validatedAt: entry.validatedAt,
    validUntil: entry.validatedAt + SNAP_FRESH_MS};
}

export async function buildSnapshotBundle(env, callBackend) {
  if (!env.SNAP) return {skipped: 'no-kv'};
  const old = await loadBundle(env.SNAP, true);
  const entries = {}, report = {};
  let backendKeys = 0;
  const save = async (method, args, payload, validatedAt = Date.now()) => {
    const key = method + ':' + JSON.stringify(args);
    if (!usable(payload)) { report[key] = payload?.error || 'empty'; return null; }
    const hash = await digest(method, payload);
    const previous = old?.entries[key];
    const same = previous?.hash === hash;
    entries[key] = {at: same ? previous.at : Date.now(), validatedAt, hash,
      payload: same ? previous.payload : payload};
    report[key] = same ? 'same' : 'changed';
    return payload.result;
  };
  const take = async (method, args) => {
    backendKeys++;
    const out = await callBackend(env, {method, args}, true);
    return save(method, args, out.payload);
  };
  // Apps Script 的首頁與獨立持股 API 使用同一個 publicTracker_；只在完整可用時衍生，否則照舊單獨查詢。
  let trackerDerived = false;
  for (const [method, args] of [['apiGetDashboard', []], ['apiListRecordDates', []],
    ['apiListMailDates', []], ['apiGetHoldingsTracker', []], ['apiGetPerformanceSeries', []]]) {
    if (method === 'apiGetHoldingsTracker' && trackerDerived) continue;
    try {
      const result = await take(method, args);
      if (method === 'apiGetDashboard' && result?.tracker &&
          Array.isArray(result.tracker.held) && Array.isArray(result.tracker.exited)) {
        await save('apiGetHoldingsTracker', [], {ok: true, result: result.tracker},
          entries['apiGetDashboard:[]'].validatedAt);
        trackerDerived = true;
        report['apiGetHoldingsTracker:[]'] += ':from-dashboard';
      }
      if (method === 'apiListMailDates') {
        const first = Array.isArray(result) && result[0];
        const date = first && (typeof first === 'string' ? first : first.date);
        if (date) await take('apiGetMailContent', [date]);
      }
    } catch { report[method + ':' + JSON.stringify(args)] = 'exception'; }
  }
  // 單鍵暫時失敗只能沿用它上次核對時間，不因其他鍵成功或心跳更新而延長。
  for (const [key, entry] of Object.entries(old?.entries || {})) {
    if (!entries[key] && SNAPPED.has(key.split(':')[0]) && Number.isFinite(entry.validatedAt) &&
        Date.now() - entry.validatedAt < SNAP_FRESH_MS && usable(entry.payload)) {
      entries[key] = entry;
      report[key] = 'recent-on-failure:' + (report[key] || 'unknown');
    }
  }
  const bundle = {version: 2, at: Date.now(), entries};
  const encoded = JSON.stringify(bundle);
  // 留足平台25MiB上限餘裕，避免內容暴增時破壞舊快照。
  if (new TextEncoder().encode(encoded).byteLength > 20 * 1024 * 1024) throw new Error('snapshot-bundle-too-large');
  await env.SNAP.put(BUNDLE_KEY, encoded, {expirationTtl: 86400});
  bundleMemo.set(env.SNAP, {at: Date.now(), value: bundle, pending: null});
  return {items: report, kvWrites: 1, kvReads: 1, backendKeys,
    bytes: new TextEncoder().encode(encoded).byteLength};
}

export class SnapshotCoordinator {
  constructor(state, env, callBackend) {
    this.store = state.storage;
    this.env = env;
    this.callBackend = callBackend;
    this.tail = Promise.resolve();
  }
  fetch(request) {
    const run = this.tail.then(() => this.handle(request));
    this.tail = run.catch(() => {});
    return run;
  }
  async handle(request) {
    if (request.method !== 'POST' || new URL(request.url).pathname !== '/refresh') return new Response('not-found', {status: 404});
    if (!this.env.SNAP) return Response.json({skipped: 'no-kv'});
    // admission先落盤；如果執行被砍或KV結果不明，該時槽仍用掉，不盲重試。
    const now = Date.now(), day = new Date(now).toISOString().slice(0, 10), slot = Math.floor(now / WRITE_SLOT_MS);
    const admission = await this.store.transaction(async tx => {
      const saved = await tx.get('budget:v2');
      const previous = saved?.day === day ? saved : {day, used: 0, lastSlot: -1};
      if (slot <= previous.lastSlot) return {skipped: 'already-checked-slot', used: previous.used};
      if (previous.used >= DAILY_WRITE_LIMIT) return {skipped: 'daily-budget', used: previous.used};
      const next = {day, used: previous.used + 1, lastSlot: slot};
      await tx.put('budget:v2', next);
      return next;
    });
    if (admission.skipped) return Response.json(admission);
    try {
      const report = await buildSnapshotBundle(this.env, this.callBackend);
      return Response.json({...report, day, reservedWrites: admission.used});
    } catch {
      return Response.json({error: 'snapshot-refresh-failed', day, reservedWrites: admission.used}, {status: 502});
    }
  }
}
