const statusEl = document.querySelector('#api-status');
const contentEl = document.querySelector('.content');
window.addEventListener('error', (event) => {
  console.error('Dashboard runtime error', event.error || event.message);
  if (statusEl) setStatus(`JS error: ${event.message || 'runtime error'}`, 'error');
});
window.addEventListener('unhandledrejection', (event) => {
  console.error('Dashboard unhandled rejection', event.reason);
  if (statusEl) setStatus(`JS error: ${event.reason?.message || event.reason || 'promise rejection'}`, 'error');
});
const titleEl = document.querySelector('.topbar h1');
const uptimeEl = document.querySelector('#uptime');
const modalEl = document.querySelector('#app-modal');
const navLinks = [...document.querySelectorAll('nav a')];
const ALL_ROUTES = ['overview', 'apps', 'news', 'stocks', 'settings'];
const DEFAULT_METRICS = ['cpu', 'ram', 'root', 'uptime', 'codex'];
const OVERVIEW_WIDGET_CATALOG = [
  { type: 'system-monitor', title: 'System Monitor', category: 'Homelab', w: 8, h: 2 },
  { type: 'web-search', title: 'Web Search', category: 'Utility', w: 6, h: 2 },
  { type: 'app-launcher', title: 'Server Apps', category: 'Homelab', w: 8, h: 4 },
  { type: 'webview', title: 'Webview', category: 'Web', w: 6, h: 5 },
  { type: 'bookmarks', title: 'Bookmarks', category: 'Web', w: 5, h: 4 },
  { type: 'clock', title: 'Clock', category: 'Utility', w: 3, h: 2 },
  { type: 'calendar', title: 'Calendar', category: 'Utility', w: 4, h: 4 },
  { type: 'weather', title: 'Weather', category: 'Utility', w: 4, h: 3 },
  { type: 'calculator', title: 'Calculator', category: 'Utility', w: 4, h: 4 },
];
const DEFAULT_OVERVIEW_WIDGETS = [
  { id: 'system', type: 'system-monitor', x: 0, y: 0, w: 8, h: 2 },
  { id: 'search', type: 'web-search', x: 0, y: 2, w: 8, h: 2 },
  { id: 'clock', type: 'clock', x: 8, y: 0, w: 4, h: 2 },
  { id: 'apps', type: 'app-launcher', x: 0, y: 4, w: 8, h: 4 },
  { id: 'webview', type: 'webview', x: 8, y: 2, w: 4, h: 5, url: 'https://chatgpt.com/' },
  { id: 'bookmarks', type: 'bookmarks', x: 0, y: 10, w: 5, h: 4 },
  { id: 'weather', type: 'weather', x: 0, y: 6, w: 4, h: 3 },
  { id: 'calendar', type: 'calendar', x: 4, y: 6, w: 4, h: 4 },
  { id: 'calculator', type: 'calculator', x: 8, y: 6, w: 4, h: 4 },
];
const WIDGET_URLS = {
  'ai-chatgpt': 'https://chatgpt.com/',
  'ai-claude': 'https://claude.ai/',
  'ai-gemini': 'https://gemini.google.com/',
  webview: 'https://chatgpt.com/',
};
const DEFAULT_BOOKMARKS = [
  { id: 'github', title: 'GitHub', url: 'https://github.com/' },
  { id: 'huggingface', title: 'Hugging Face', url: 'https://huggingface.co/' },
  { id: 'youtube', title: 'YouTube', url: 'https://youtube.com/' },
];
let overviewGrid = null;
let overviewWidgetTimers = [];

let apps = [];
let metrics = null;
let selectedApp = null;
let agents = [];
let missionControl = null;
let newsletters = [];
let dailyCuration = null;
let newsletterSources = [];
let newsSourceFilter = '';
let newsVisibleLimit = 10;
const NEWS_PAGE_SIZE = 10;
const NEWS_MAX_VISIBLE = 60;
let settings = null;
let ops = null;
let stockAudit = null;
let stockReviewOptions = null;
let stockSelectedReviewId = '';
let stockSelectedTicker = '';
let currentRoute = routeFromHash();
let overviewPinnedAppIds = [];
let agentTokenData = readStoredAgentTokenData();
let missionControlRefreshInFlight = false;
const refreshTimers = { mission: null, metrics: null, apps: null, news: null };

async function api(path, options) {
  const response = await fetch(path, options);
  const contentType = response.headers.get('content-type') || '';
  const body = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof body === 'object' ? body.detail : null;
    const message = typeof detail === 'string' ? detail : detail?.message || `${response.status} ${response.statusText}`;
    const error = new Error(message);
    error.detail = detail;
    throw error;
  }
  return body;
}

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function decodeHtmlEntities(value = '') {
  const named = {
    amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
    rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“', mdash: '—', ndash: '–', hellip: '…',
  };
  return String(value)
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCodePoint(parseInt(code, 16)))
    .replace(/&([a-z]+);/gi, (match, name) => Object.prototype.hasOwnProperty.call(named, name.toLowerCase()) ? named[name.toLowerCase()] : match);
}

function plainReviewText(value = '') {
  return decodeHtmlEntities(String(value)
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, ' ')
    .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, ' ')
    .replace(/<(br|hr)\s*\/?>/gi, ' ')
    .replace(/<\/(p|div|h[1-6]|li|blockquote|figure|tr)>/gi, ' ')
    .replace(/<[^>]+>/g, ' '))
    .replace(/\s+/g, ' ')
    .trim();
}

function reviewTextHtml(value = '', fallback = '—') {
  const cleaned = plainReviewText(value);
  return escapeHtml(cleaned || fallback);
}

function routeFromHash() {
  const raw = (window.location.hash || '').replace('#', '');
  const navigation = settings ? navigationPrefs() : { visible_tabs: ALL_ROUTES, landing_tab: 'overview' };
  const landing = navigation.visible_tabs.includes(navigation.landing_tab) ? navigation.landing_tab : (navigation.visible_tabs[0] || 'overview');
  const route = raw || landing || 'overview';
  return navigation.visible_tabs.includes(route) ? route : landing || 'overview';
}

function setStatus(text, state = '') {
  statusEl.textContent = text;
  statusEl.className = `api-status ${state}`.trim();
}

function metricCard(label, value, detail = '', id = '') {
  const drag = id ? ` draggable="true" data-metric-id="${escapeHtml(id)}"` : '';
  return `<article class="metric-card draggable-card"${drag}><span>${label}</span><strong>${value}</strong><small>${detail}</small></article>`;
}

function readLocalArray(key, fallback = []) {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || 'null');
    return Array.isArray(parsed) ? parsed : fallback;
  } catch (_) {
    return fallback;
  }
}

function writeLocalArray(key, value) {
  localStorage.setItem(key, JSON.stringify(value));
}

function prefs(section = '', fallback = {}) {
  const root = settings?.preferences || {};
  return section ? { ...fallback, ...(root[section] || {}) } : root;
}

function selectedFormValues(form, name) {
  return [...form.querySelectorAll(`[name="${name}"]:checked`)].map((item) => item.value);
}

function preferenceNumber(value, fallback, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

function preferenceBool(value) {
  return value === true || value === 'on' || value === 'true';
}

function navigationPrefs() {
  const navigation = prefs('navigation', { landing_tab: 'overview', visible_tabs: ALL_ROUTES });
  const visible = Array.isArray(navigation.visible_tabs) ? navigation.visible_tabs.filter((route) => ALL_ROUTES.includes(route)) : ALL_ROUTES;
  if (!visible.includes('settings')) visible.push('settings');
  if (!visible.length) visible.push('overview', 'settings');
  return { ...navigation, visible_tabs: [...new Set(visible)] };
}

function newsPrefs() {
  return prefs('news', { default_source: '', page_size: 10, curated_categories: ['Semiconductor', 'Stocks', 'AI'], hide_roundups: true });
}

function isRoundupTitle(title = '') {
  return /week in review|weekly review|daily roundup|weekly roundup|roundup|headlines|news digest|link digest/i.test(String(title));
}

function visibleNewsItems(items = newsletters) {
  const news = newsPrefs();
  const rows = news.hide_roundups ? items.filter((item) => !isRoundupTitle(item.title)) : items;
  return rows;
}

function readStoredAgentTokenData() {
  const fallback = { codex_token_usage: null, codex_token_usage_label: null, codex_token_usage_window: null, codex_token_usage_resets: null, codex_next_reset_iso: null, codex_weekly_next_reset_iso: null, codex_token_usage_error: null, codex_tokens_live: null, last_update: null, refreshed_at: null };
  try {
    const parsed = JSON.parse(localStorage.getItem('agentTokenData') || 'null');
    if (!parsed || typeof parsed !== 'object') return fallback;
    const refreshedAt = parsed.refreshed_at ? Date.parse(parsed.refreshed_at) : NaN;
    const stale = !Number.isFinite(refreshedAt) || Date.now() - refreshedAt > 10 * 60 * 1000;
    const errorOnly = parsed.codex_token_usage_error && parsed.codex_token_usage === null && !parsed.codex_token_usage_label;
    return stale || errorOnly ? fallback : { ...fallback, ...parsed };
  } catch (_) {
    return fallback;
  }
}

function updateAgentTokenDataFromProject(project) {
  if (!project) return;
  const now = new Date();
  const hours = String(now.getHours()).padStart(2, '0');
  const mins = String(now.getMinutes()).padStart(2, '0');
  agentTokenData = {
    ...agentTokenData,
    codex_token_usage: project.codex_token_usage,
    codex_token_usage_label: project.codex_token_usage_label,
    codex_token_usage_window: project.codex_token_usage_window,
    codex_token_usage_resets: project.codex_token_usage_resets,
    codex_next_reset_iso: project.codex_next_reset_iso || null,
    codex_weekly_next_reset_iso: project.codex_weekly_next_reset_iso || null,
    codex_token_usage_error: project.token_usage_errors?.codex || null,
    codex_tokens_live: project.codex_tokens_live ?? null,
    refreshed_at: project.token_usage_refreshed_at || null,
    last_update: `${hours}:${mins}`,
  };
  localStorage.setItem('agentTokenData', JSON.stringify(agentTokenData));
}

function orderedIds(key, defaults, available) {
  const saved = readLocalArray(key, defaults);
  const allowed = new Set(available);
  const ordered = saved.filter((id) => allowed.has(id));
  defaults.forEach((id) => { if (allowed.has(id) && !ordered.includes(id)) ordered.push(id); });
  return ordered;
}

function oneDecimal(value, fallback = '—') {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(1) : fallback;
}

function formatCpuPct(value) {
  return `${oneDecimal(value, '0.0')}%`;
}

function formatNetworkRate(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  if (n >= 1) return `${n.toFixed(1)} MB/s`;
  if (n >= 0.01) return `${Math.round(n * 1024)} KB/s`;
  return `${(n * 1024).toFixed(1)} KB/s`;
}

function formatTokenCount(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(Math.round(n));
}

function formatMetricUpdate(value) {
  if (!value) return agentTokenData.last_update ? `Refreshed ${agentTokenData.last_update}` : 'refresh pending';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return agentTokenData.last_update ? `Refreshed ${agentTokenData.last_update}` : 'refresh pending';
  const hours = String(date.getHours()).padStart(2, '0');
  const mins = String(date.getMinutes()).padStart(2, '0');
  return `Refreshed ${hours}:${mins}`;
}

function tokenMetricDetail(tokens, refreshedAt, windowLabel, resets) {
  const parts = [formatMetricUpdate(refreshedAt), formatTokenCount(tokens) + ' tokens'];
  if (windowLabel) parts.push(windowLabel);
  if (resets) parts.push(`resets ${resets}`);
  return parts.join(' · ');
}

function formatCodexResetTime(resetStr) {
  if (!resetStr) return '';
  const trimmed = resetStr.trim();
  const timeOptions = { hour: 'numeric', minute: '2-digit', hour12: true, timeZoneName: 'short' };
  
  const hmMatch = trimmed.match(/^(\d{1,2}):(\d{2})$/);
  if (hmMatch) {
    const hours = parseInt(hmMatch[1], 10);
    const minutes = parseInt(hmMatch[2], 10);
    if (hours >= 0 && hours <= 23 && minutes >= 0 && minutes <= 59) {
      // Codex /status reports bare HH:MM reset times in UTC; render them in the browser's local timezone.
      const now = new Date();
      const resetDate = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hours, minutes, 0, 0));
      if (resetDate <= now) return 'pending';
      return resetDate.toLocaleTimeString(undefined, timeOptions);
    }
  }
  
  const inMatch = trimmed.match(/^in\s+(\d+)\s*([hm])$/i);
  if (inMatch) {
    const value = parseInt(inMatch[1], 10);
    const unit = inMatch[2].toLowerCase();
    const ms = unit === 'h' ? value * 60 * 60 * 1000 : value * 60 * 1000;
    const resetDate = new Date(Date.now() + ms);
    return resetDate.toLocaleTimeString(undefined, timeOptions);
  }
  
  return trimmed;
}

function codexResetCountdown(nextResetIso) {
  if (!nextResetIso) return null;
  const ms = Date.parse(nextResetIso) - Date.now();
  if (!Number.isFinite(ms) || ms <= 0) return 'reset pending';
  const maxFiveHourMs = 5 * 60 * 60 * 1000;
  if (ms > maxFiveHourMs) return null;
  const totalMinutes = Math.ceil(ms / 60000);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours > 0) return `refresh in ${hours}h ${minutes}m`;
  return `refresh in ${minutes}m`;
}

function codexMetricDetail(windowLabel, resets, nextResetIso) {
  const countdown = codexResetCountdown(nextResetIso);
  const parts = [];
  if (countdown) parts.push(countdown);
  else if (windowLabel) parts.push(windowLabel);
  if (resets) {
    const formatted = formatCodexResetTime(resets);
    if (formatted && formatted !== 'pending') parts.push(`resets ${formatted}`);
  }
  if (!parts.length && agentTokenData.codex_token_usage_error) parts.push(agentTokenData.codex_token_usage_error.includes('auth') ? 'Codex auth needs sign-in' : 'Official /status refresh pending');
  if (!parts.length) parts.push('Reading official Codex /status…');
  if (prefs('overview', {}).show_codex_local_tokens !== false && agentTokenData.codex_tokens_live !== null && agentTokenData.codex_tokens_live !== undefined) parts.push(`${formatTokenCount(agentTokenData.codex_tokens_live)} local tokens`);
  return parts.join(' · ') || '—';
}

function formatTokenPercent(value, label) {
  if (label) return label;
  if (value === null || value === undefined || value === '') {
    if (agentTokenData.codex_token_usage_error?.toLowerCase().includes('auth')) return 'Sign in required';
    return agentTokenData.codex_token_usage_error ? 'Refresh pending' : 'Checking…';
  }
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(1)}%` : 'Checking…';
}

function formatDateTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat(undefined, {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: 'numeric', minute: '2-digit', hour12: true,
    timeZoneName: 'short',
  }).formatToParts(date);
  const get = (type) => parts.find((p) => p.type === type)?.value || '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')} ${get('dayPeriod') || ''} ${get('timeZoneName') || ''}`.replace(/\s+/g, ' ').trim();
}

function formatDateOnly(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, { year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
}

function humanCronSchedule(schedule = '') {
  if (!schedule) return 'Hermes cron job';
  if (!/^\d+ \d+ \* \* \*$/.test(schedule.trim())) return schedule;
  const [minuteRaw, hourRaw] = schedule.trim().split(/\s+/);
  const minute = Number(minuteRaw);
  const hour = Number(hourRaw);
  if (!Number.isFinite(minute) || !Number.isFinite(hour)) return schedule;
  const suffix = hour >= 12 ? 'PM' : 'AM';
  const hour12 = ((hour + 11) % 12) + 1;
  return `Daily at ${hour12}:${String(minute).padStart(2, '0')} ${suffix}`;
}

const METRIC_BUILDERS = {
  cpu: (data) => metricCard('CPU', formatCpuPct(data.cpu_pct), data.cpu_temp_c ? `${oneDecimal(data.cpu_temp_c)}°C` : 'temperature n/a', 'cpu'),
  ram: (data) => metricCard('RAM', `${oneDecimal(data.ram_pct)}%`, `${oneDecimal(data.ram_used_gb)} GB of ${oneDecimal(data.ram_total_gb)} GB`, 'ram'),
  root: (data) => metricCard('Root disk', `${oneDecimal(data.disk_pct)}%`, `${oneDecimal(data.disk_used_gb)} GB of ${oneDecimal(data.disk_total_gb)} GB`, 'root'),
  uptime: (data) => metricCard('Uptime', `${oneDecimal(data.uptime_hours)} h`, 'since boot', 'uptime'),
  download: (data) => metricCard('Download', formatNetworkRate(data.network?.download_mb_s), `${oneDecimal(data.network?.recv_gb)} GB received`, 'download'),
  upload: (data) => metricCard('Upload', formatNetworkRate(data.network?.upload_mb_s), `${oneDecimal(data.network?.sent_gb)} GB sent`, 'upload'),
  codex: (data) => metricCard('Codex', formatTokenPercent(agentTokenData.codex_token_usage, agentTokenData.codex_token_usage_label), codexMetricDetail(agentTokenData.codex_token_usage_window, agentTokenData.codex_token_usage_resets, agentTokenData.codex_next_reset_iso || agentTokenData.codex_weekly_next_reset_iso || null), 'codex'),
};

function metricsHtml(data) {
  if (!data) return '<div class="empty">Metrics loading…</div>';
  const requested = prefs('overview', { metrics: DEFAULT_METRICS }).metrics || DEFAULT_METRICS;
  const selected = [...new Set(requested)].filter((id) => METRIC_BUILDERS[id]);
  const saved = readLocalArray('overviewMetricOrder', DEFAULT_METRICS)
    .map((id) => id === 'network' ? null : id)
    .filter((id) => id && id !== 'docker' && id !== 'claude' && selected.includes(id));
  const order = [...saved, ...selected.filter((id) => !saved.includes(id))];
  return order.length ? order.map((id) => METRIC_BUILDERS[id](data)).join('') : '<div class="empty">No Overview metrics selected. Open Settings to enable cards.</div>';
}

function overviewAppIds() {
  return Array.isArray(overviewPinnedAppIds) ? overviewPinnedAppIds : [];
}

async function setOverviewAppIds(ids) {
  const filtered = [...new Set(ids)].filter((id) => apps.some((app) => app.id === id));
  overviewPinnedAppIds = filtered;
  if (settings) settings.pinned_app_ids = filtered;
  try {
    const result = await api('/api/settings', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ pinned_app_ids: filtered }) });
    settings = result.settings;
    overviewPinnedAppIds = settings.pinned_app_ids || [];
    setStatus('Pinned apps synced', 'ok');
  } catch (error) {
    setStatus(`Pin sync failed: ${error.message}`, 'error');
  }
}

function overviewApps() {
  const ids = overviewAppIds();
  const byId = Object.fromEntries(apps.map((app) => [app.id, app]));
  return ids.map((id) => byId[id]).filter(Boolean);
}

function overviewAvailableApps() {
  const selected = new Set(overviewAppIds());
  return apps.filter((app) => !selected.has(app.id));
}
function statusClass(status) {
  if (status === 'running') return 'running';
  if (status === 'stopped' || status === 'missing') return 'stopped';
  return 'unknown';
}

function healthClass(health) {
  if (!health || health.ok === null) return 'unknown';
  return health.ok ? 'running' : 'stopped';
}

function healthLabel(app) {
  if (!app.health || app.health.ok === null) return app.web_ui_port ? 'not checked' : 'no web UI';
  return app.health.ok ? `web ${app.health.http_code || 'ok'}` : app.health.status;
}

function serviceState(app) {
  if (app.status === 'running' && app.health?.ok !== false) return { label: 'Up', className: 'running', detail: app.health?.ok ? 'web reachable' : 'container running' };
  if (app.status === 'running' && app.health?.ok === false) return { label: 'Web down', className: 'stopped', detail: app.health.status || 'web check failed' };
  if (app.status === 'stopped' || app.status === 'missing') return { label: 'Down', className: 'stopped', detail: app.status };
  return { label: 'Unknown', className: 'unknown', detail: app.status || 'not checked' };
}

function fallbackInitial(name) {
  return escapeHtml((name || '?').trim().slice(0, 1).toUpperCase());
}

function openHref(app) {
  return app.open_url || app.web_ui_url;
}

function appIconHtml(app) {
  const initial = fallbackInitial(app.name);
  return app.icon_url
    ? `<img src="${app.icon_url}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'), {className:'app-fallback', textContent:'${initial}'}))" />`
    : `<div class="app-fallback">${initial}</div>`;
}

function appMatches(app, query, status, health) {
  const haystack = `${app.name} ${app.description || ''} ${app.id}`.toLowerCase();
  const healthOk = !health || (health === 'ok' ? app.health?.ok === true : health === 'bad' ? app.health?.ok === false : app.health?.ok === null);
  return (!query || haystack.includes(query))
    && (!status || app.status === status)
    && healthOk;
}

function overviewPickerItem(app) {
  const pinned = overviewAppIds().includes(app.id);
  return `<article class="available-app ${pinned ? 'is-pinned' : ''}" draggable="true" data-available-app-id="${escapeHtml(app.id)}">
    <div class="app-icon small-icon">${appIconHtml(app)}</div><div><strong>${escapeHtml(app.name)}</strong><small>${escapeHtml(app.description || app.id)}</small></div><button class="button ${pinned ? 'danger' : 'secondary'}" data-overview-${pinned ? 'remove' : 'add'}="${escapeHtml(app.id)}">${pinned ? 'Unpin' : 'Pin'}</button>
  </article>`;
}

function appCard(app, mode = 'overview') {
  const href = openHref(app);
  const dragAttrs = mode === 'overview' ? ` draggable="true" data-overview-app-card="${escapeHtml(app.id)}"` : '';
  const open = href ? `<a class="button" href="${href}" target="_blank" rel="noreferrer">Open</a>` : '<span class="button disabled">No UI</span>';
  const description = app.description || app.id;
  const overviewMode = mode === 'overview';
  const managerActions = mode === 'manager'
    ? `<button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="start">Start</button>
       <button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="stop">Stop</button>
       <button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="restart">Restart</button>
       <button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="logs">Logs</button>`
    : '';
  const state = serviceState(app);
  return `<article class="app-card ${mode === 'manager' ? 'app-card-manager' : ''}"${dragAttrs}>
    <div class="app-icon">${appIconHtml(app)}</div>
    <button class="app-main app-select" data-app-id="${escapeHtml(app.id)}" data-action="details">
      <div class="app-title"><h3>${escapeHtml(app.name)}</h3>${overviewMode ? `<span class="service-pill ${state.className}" title="${escapeHtml(state.detail)}"><span class="dot ${state.className}"></span>${escapeHtml(state.label)}</span>` : `<span class="dot ${statusClass(app.status)}" title="container ${escapeHtml(app.status)}"></span><span class="dot ${healthClass(app.health)}" title="${escapeHtml(healthLabel(app))}"></span>`}</div>
      <p>${escapeHtml(description)}</p>
      ${overviewMode ? '' : `<div class="app-meta"><span>${escapeHtml(app.status)}</span><span>${escapeHtml(healthLabel(app))}</span>${app.web_ui_port ? `<span>:${app.web_ui_port}</span>` : ''}</div>`}
    </button>
    <div class="app-actions">${open}<button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="details">Manage</button>${managerActions}</div>
  </article>`;
}

function updateNav() {
  const navigation = settings ? navigationPrefs() : { visible_tabs: ALL_ROUTES };
  if (settings && !navigation.visible_tabs.includes(currentRoute)) {
    currentRoute = routeFromHash();
  }
  navLinks.forEach((link) => {
    const route = link.getAttribute('href').replace('#', '');
    link.hidden = !navigation.visible_tabs.includes(route);
    link.classList.toggle('active', route === currentRoute);
  });
  titleEl.textContent = ({ overview: 'Overview', apps: 'Apps', news: 'Newsletters', stocks: 'Stocks', settings: 'Settings' })[currentRoute];
}

function overviewWidgetCatalogItem(type) {
  return OVERVIEW_WIDGET_CATALOG.find((item) => item.type === type)
    || (WIDGET_URLS[type] ? OVERVIEW_WIDGET_CATALOG.find((item) => item.type === 'webview') : null)
    || OVERVIEW_WIDGET_CATALOG[0];
}

function normalizeOverviewWidget(widget) {
  if (!widget || typeof widget !== 'object') return widget;
  if (WIDGET_URLS[widget.type] && widget.type !== 'webview') {
    return { ...widget, type: 'webview', url: WIDGET_URLS[widget.type] };
  }
  return widget;
}

function readOverviewWidgets() {
  try {
    const saved = JSON.parse(localStorage.getItem('overviewWidgets') || 'null');
    if (Array.isArray(saved) && saved.length) return saved.map(normalizeOverviewWidget);
  } catch (_) {}
  return DEFAULT_OVERVIEW_WIDGETS.map((item) => ({ ...item }));
}

function writeOverviewWidgets(widgets) {
  localStorage.setItem('overviewWidgets', JSON.stringify(widgets));
}

function cleanupOverviewWidgets() {
  overviewWidgetTimers.forEach((timer) => clearInterval(timer));
  overviewWidgetTimers = [];
  if (overviewGrid) {
    overviewGrid.destroy(false);
    overviewGrid = null;
  }
}

function renderOverview() {
  cleanupOverviewWidgets();
  contentEl.innerHTML = `<section class="grid-stack overview-widget-grid" id="overview-widget-grid"></section>
    <section class="widget-manager panel" id="widget-manager" hidden>
      <div class="panel-head compact-head"><div><p class="eyebrow">Widgets</p><h2>Add widgets</h2></div></div>
      <div class="widget-catalog">${OVERVIEW_WIDGET_CATALOG.map(widgetCatalogCard).join('')}</div>
    </section>
    <nav class="overview-floating-nav" aria-label="Overview controls">
      <form class="overview-web-search" id="overview-search-form">
        <input id="overview-search" type="search" placeholder="Search the web…" autocomplete="off" />
        <button class="button primary" type="submit">Search</button>
      </form>
      <button class="button secondary" data-add-widget="true">Add widget</button>
    </nav>`;
  initOverviewGrid();
}

function widgetCatalogCard(item) {
  return `<button class="widget-catalog-card" data-widget-type="${escapeHtml(item.type)}">
    <span>${escapeHtml(item.category)}</span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.w)}×${escapeHtml(item.h)}</small>
  </button>`;
}

function initOverviewGrid() {
  const gridEl = document.querySelector('#overview-widget-grid');
  if (!gridEl || !window.GridStack) {
    if (gridEl) gridEl.innerHTML = '<div class="empty">Widget grid library is loading. Refresh once if this stays visible.</div>';
    return;
  }
  const widgets = readOverviewWidgets();
  overviewGrid = GridStack.init({
    column: 12,
    cellHeight: 82,
    margin: 16,
    float: false,
    resizable: { handles: 'e, se, s, sw, w' },
    draggable: { handle: '.widget-drag-handle' },
  }, gridEl);
  widgets.forEach((widget) => addOverviewWidget(widget, { save: false }));
  overviewGrid.on('change', () => persistOverviewGrid());
}

function addOverviewWidget(widget, { save = true } = {}) {
  const catalog = overviewWidgetCatalogItem(widget.type);
  const id = widget.id || `${widget.type}-${Date.now()}`;
  const node = {
    id,
    x: widget.x,
    y: widget.y,
    w: widget.w || catalog.w,
    h: widget.h || catalog.h,
    minW: 2,
    minH: 2,
    content: `<div class="overview-widget" data-widget-id="${escapeHtml(id)}" data-widget-type="${escapeHtml(widget.type)}">
      <header class="overview-widget-head"><button class="widget-drag-handle" title="Move widget">⠿</button><strong>${escapeHtml(widget.title || catalog.title)}</strong><button class="icon-button danger" data-delete-widget="${escapeHtml(id)}" title="Delete widget">×</button></header>
      <div class="overview-widget-body">${widgetBodyHtml(widget.type, widget)}</div>
    </div>`,
  };
  overviewGrid.addWidget(node);
  hydrateWidget(id, widget.type);
  if (save) persistOverviewGrid();
}

function persistOverviewGrid() {
  if (!overviewGrid) return;
  const widgets = overviewGrid.engine.nodes.map((node) => {
    const el = node.el?.querySelector('[data-widget-type]');
    const type = el?.dataset.widgetType || 'web-search';
    const id = el?.dataset.widgetId || node.id;
    return { id, type, x: node.x, y: node.y, w: node.w, h: node.h };
  });
  writeOverviewWidgets(widgets);
}

function safeUrl(value, fallback = WIDGET_URLS.webview) {
  const trimmed = String(value || '').trim();
  if (!trimmed) return fallback;
  return /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

function readWebviewUrls() {
  try {
    const saved = JSON.parse(localStorage.getItem('overviewWebviewUrls') || '{}');
    return saved && typeof saved === 'object' ? saved : {};
  } catch (_) {
    return {};
  }
}

function writeWebviewUrl(id, url) {
  const urls = readWebviewUrls();
  urls[id] = safeUrl(url);
  localStorage.setItem('overviewWebviewUrls', JSON.stringify(urls));
}

function webviewUrlFor(widget = {}) {
  const urls = readWebviewUrls();
  return urls[widget.id] || widget.url || WIDGET_URLS[widget.type] || WIDGET_URLS.webview;
}

function bookmarkInitial(title = '') {
  return escapeHtml((title || '?').trim().slice(0, 2).toUpperCase());
}

function readBookmarks() {
  try {
    const saved = JSON.parse(localStorage.getItem('overviewBookmarks') || 'null');
    if (Array.isArray(saved) && saved.length) return saved;
  } catch (_) {}
  return DEFAULT_BOOKMARKS.map((item) => ({ ...item }));
}

function writeBookmarks(bookmarks) {
  localStorage.setItem('overviewBookmarks', JSON.stringify(bookmarks));
}

function bookmarksHtml() {
  const rows = readBookmarks();
  return `<div class="bookmark-widget">
    <div class="bookmark-icon-grid">${rows.map((item) => `<article class="bookmark-icon-card"><a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer" title="${escapeHtml(item.title)}"><span>${bookmarkInitial(item.title)}</span><strong>${escapeHtml(item.title)}</strong></a><button class="icon-button danger" data-delete-bookmark="${escapeHtml(item.id)}" title="Delete bookmark">×</button></article>`).join('')}<button class="bookmark-icon-card add-bookmark-card" data-toggle-bookmark-form="true"><span>+</span><strong>Add</strong></button></div>
    <form class="bookmark-form" hidden>
      <input name="title" placeholder="Site name" />
      <input name="url" placeholder="example.com" />
      <button class="button primary" type="submit">Add</button>
    </form>
  </div>`;
}


function systemMonitorHtml() {
  return `<div class="system-monitor-widget" id="overview-system-monitor">${metricsHtml(metrics)}</div>`;
}

function evaluateCalculatorExpression(expression) {
  const cleaned = String(expression || '').replace(/[×]/g, '*').replace(/[÷]/g, '/').trim();
  if (!cleaned) return '';
  if (!/^[\d\s+\-*/().%]+$/.test(cleaned)) return 'Invalid expression';
  try {
    const value = Function(`"use strict"; return (${cleaned})`)();
    return Number.isFinite(Number(value)) ? String(value) : 'Invalid expression';
  } catch (_) {
    return '';
  }
}

function widgetBodyHtml(type, widget = {}) {
  if (type === 'system-monitor') return systemMonitorHtml();
  if (type === 'web-search') return `<form class="widget-search-form"><input type="search" placeholder="Search Google…" /><button class="button primary" type="submit">Go</button></form>`;
  if (type === 'app-launcher') return `<div id="overview-app-grid" class="app-grid overview-grid"></div>`;
  if (type === 'clock') return `<div class="clock-widget"><strong data-clock-time>--:--</strong><small data-clock-date>—</small></div>`;
  if (type === 'calendar') return `<div class="calendar-widget" data-calendar-widget></div>`;
  if (type === 'weather') return `<div class="weather-widget" data-weather-widget><strong>Weather</strong><small>Loading…</small></div>`;
  if (type === 'calculator') return calculatorHtml();
  if (type === 'bookmarks') return bookmarksHtml();
  const url = webviewUrlFor({ ...widget, type });
  return `<div class="webview-widget" data-webview-widget="true">
    <form class="webview-toolbar">
      <button class="button secondary" type="button" data-webview-action="back">←</button>
      <button class="button secondary" type="button" data-webview-action="forward">→</button>
      <button class="button secondary" type="button" data-webview-action="refresh">↻</button>
      <input name="url" value="${escapeHtml(url)}" placeholder="https://example.com" />
      <button class="button primary" type="submit">Go</button>
    </form>
    <iframe src="${escapeHtml(url)}" title="${escapeHtml(type)}" loading="lazy"></iframe>
  </div>`;
}

function hydrateWidget(id, type) {
  const root = document.querySelector(`[data-widget-id="${CSS.escape(id)}"]`);
  if (!root) return;
  if (type === 'system-monitor') root.querySelector('.system-monitor-widget').innerHTML = metricsHtml(metrics);
  if (type === 'app-launcher') drawOverviewApps();
  if (type === 'clock') {
    const tick = () => {
      const now = new Date();
      root.querySelector('[data-clock-time]').textContent = now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' });
      root.querySelector('[data-clock-date]').textContent = now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric' });
    };
    tick();
    overviewWidgetTimers.push(setInterval(tick, 1000));
  }
  if (type === 'calendar') renderCalendar(root.querySelector('[data-calendar-widget]'));
  if (type === 'weather') renderWeather(root.querySelector('[data-weather-widget]'));
}

function renderCalendar(el) {
  if (!el) return;
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth();
  const first = new Date(year, month, 1);
  const days = new Date(year, month + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < first.getDay(); i += 1) cells.push('<span></span>');
  for (let day = 1; day <= days; day += 1) cells.push(`<strong class="${day === now.getDate() ? 'today' : ''}">${day}</strong>`);
  el.innerHTML = `<header><strong>${now.toLocaleDateString([], { month: 'long', year: 'numeric' })}</strong></header><div class="calendar-days"><span>S</span><span>M</span><span>T</span><span>W</span><span>T</span><span>F</span><span>S</span>${cells.join('')}</div>`;
}

async function renderWeather(el) {
  if (!el) return;
  try {
    const pos = await new Promise((resolve, reject) => {
      if (!navigator.geolocation) { reject(new Error('No location')); return; }
      navigator.geolocation.getCurrentPosition(resolve, reject, { timeout: 4000, maximumAge: 900000 });
    });
    const { latitude, longitude } = pos.coords;
    const data = await fetch(`https://api.open-meteo.com/v1/forecast?latitude=${latitude}&longitude=${longitude}&current=temperature_2m,relative_humidity_2m,wind_speed_10m`).then((res) => res.json());
    const c = data.current || {};
    el.innerHTML = `<strong>${Math.round(c.temperature_2m)}°C</strong><small>${Math.round(c.relative_humidity_2m)}% humidity · ${Math.round(c.wind_speed_10m)} km/h wind</small>`;
  } catch (_) {
    el.innerHTML = '<strong>Weather</strong><small>Allow location to show local weather.</small>';
  }
}

function calculatorHtml() {
  return `<form class="calculator-widget">
    <input name="expression" type="text" inputmode="decimal" autocomplete="off" placeholder="Type a calculation, e.g. 4+5" />
    <output data-calc-result>—</output>
  </form>`;
}


function drawOverviewApps() {
  const gridEl = document.querySelector('#overview-app-grid');
  const availableEl = document.querySelector('#available-app-list');
  if (!gridEl) return;
  const selectedIds = overviewAppIds();
  const selected = selectedIds.length ? overviewApps() : apps.slice(0, 8);
  gridEl.innerHTML = selected.length
    ? selected.map((app) => appCard(app)).join('')
    : '<div class="empty">No apps available.</div>';
  if (availableEl) availableEl.innerHTML = apps.length ? apps.map(overviewPickerItem).join('') : '<div class="empty compact">No apps available.</div>';
}

function renderAppsPage() {
  const running = apps.filter((app) => app.status === 'running').length;
  const stopped = apps.filter((app) => app.status === 'stopped').length;
  const healthy = apps.filter((app) => app.health?.ok === true).length;
  const unhealthy = apps.filter((app) => app.health?.ok === false).length;
  contentEl.innerHTML = `<section class="manager-hero">
      ${metricCard('Apps', apps.length, `${apps.filter((app) => app.web_ui_port).length} with web UI`)}
      ${metricCard('Running', running, 'containers active')}
      ${metricCard('Web healthy', healthy, `${unhealthy} failing checks`)}
      ${metricCard('Stopped', stopped, 'containers inactive')}
    </section>
    <section class="panel">
      <div class="panel-head manager-head">
        <div>
          <p class="eyebrow">App Manager</p>
          <h2>Manage compose apps</h2>
        </div>
        <div class="manager-filters">
          <input id="apps-search" type="search" placeholder="Search apps…" />
          <select id="status-filter"><option value="">All statuses</option><option value="running">Running</option><option value="stopped">Stopped</option><option value="unknown">Unknown</option><option value="missing">Missing</option></select>
          <select id="health-filter"><option value="">All health</option><option value="ok">Web healthy</option><option value="bad">Web failing</option><option value="none">No UI / unchecked</option></select>
          <button class="button secondary" data-refresh-apps="true">Refresh</button>
          <button class="button primary" data-add-app="true">Add App</button>
        </div>
      </div>
      <div class="hint-row">Only Docker/compose-managed apps are shown here. Non-container services are intentionally excluded.</div>
      <div id="apps-manager-grid" class="app-grid manager-grid"></div>
    </section>`;

  const searchEl = document.querySelector('#apps-search');
  const statusFilterEl = document.querySelector('#status-filter');
  const healthFilterEl = document.querySelector('#health-filter');
  const gridEl = document.querySelector('#apps-manager-grid');
  function draw() {
    const query = searchEl.value.trim().toLowerCase();
    const visible = apps.filter((app) => appMatches(app, query, statusFilterEl.value, healthFilterEl.value));
    gridEl.innerHTML = visible.length ? visible.map((app) => appCard(app, 'manager')).join('') : '<div class="empty">No apps match those filters.</div>';
  }
  [searchEl, statusFilterEl, healthFilterEl].forEach((el) => el.addEventListener('input', draw));
  document.querySelector('[data-refresh-apps]').addEventListener('click', refreshApps);
  document.querySelector('[data-add-app]').addEventListener('click', openAddAppModal);
  draw();
}

function compactCount(value, label) {
  return `${value ?? '—'} ${label}`;
}

function agentStatusDot(status = 'unknown') {
  const normalized = ['running', 'active', 'success', 'ok'].includes(status) ? 'running' : ['error', 'stopped', 'paused', 'not-configured'].includes(status) ? 'stopped' : 'unknown';
  return `<span class="dot ${normalized}" title="${escapeHtml(status)}"></span>`;
}

function agentCard({ title, subtitle, meta = [], status = 'unknown', kind = 'agent', actions = '' }) {
  return `<article class="mission-card">
    <div class="mission-card-head"><span class="agent-avatar">${fallbackInitial(title)}</span><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(subtitle || kind)}</small></div>${agentStatusDot(status)}</div>
    <div class="agent-meta-list">${meta.filter(Boolean).map((item) => `<span>${escapeHtml(item)}</span>`).join('')}</div>
    ${actions ? `<div class="mission-actions">${actions}</div>` : ''}
  </article>`;
}

function renderProfiles(mc) {
  const inventory = Object.fromEntries((mc.profile_inventory || []).map((item) => [item.profile, item]));
  return (mc.profiles || []).map((profile) => {
    const inv = inventory[profile.id] || {};
    return agentCard({
      title: profile.name,
      subtitle: profile.active ? 'active Hermes profile' : 'Hermes profile',
      status: profile.status,
      kind: 'profile',
      meta: [profile.model, profile.gateway, `${inv.skills_count ?? 0} skills`, `${(inv.scripts || []).length} scripts`, `${(inv.logs || []).length} logs`],
      actions: `<button class="button secondary" data-profile-detail="${escapeHtml(profile.id)}">Details</button>`,
    });
  }).join('') || '<div class="empty">No Hermes profiles discovered.</div>';
}

function agentOfficeProfileCard(profile, inv = {}) {
  return agentCard({
    title: profile.name,
    subtitle: profile.active ? 'main agent · attached profile' : 'main agent profile',
    status: profile.status,
    kind: 'profile',
    meta: [profile.model, profile.gateway, `${inv.skills_count ?? 0} skills`],
    actions: `<button class="button secondary" data-profile-detail="${escapeHtml(profile.id)}">Details</button>`,
  });
}

function renderOfficeSubagents(profileId, mc) {
  const configured = (mc.configured_agents || []).filter((agent) => {
    if (agent.script) return false;
    if (!agent.auto_discovered) return profileId === 'dashcraft';
    if (agent.origin === 'project') return profileId === 'dashcraft';
    return agent.origin === `profile:${profileId}`;
  });
  const rows = [
    ...configured.map((agent) => ({
      id: agent.id,
      name: agent.name || agent.id,
      description: agent.description || 'configured dashboard sub-agent',
      status: agent.status || 'idle',
      action: `<button class="button secondary" data-agent-id="${escapeHtml(agent.id)}" data-agent-action="history">History</button>`,
    })),
  ];
  if (!rows.length) return '<div class="empty compact">No sub-agents discovered in this office yet.</div>';
  return rows.map((agent) => agentCard({
    title: agent.name,
    subtitle: agent.description,
    status: agent.status,
    kind: 'sub-agent',
    meta: [agent.status || 'available'],
    actions: agent.action,
  })).join('');
}

function renderOfficeCronJobs(profileId, mc) {
  const jobs = (mc.cron_jobs || []).filter((job) => (job.profile || 'dashcraft') === profileId);
  if (!jobs.length) return '<div class="empty compact">No cron jobs are assigned to this office.</div>';
  return jobs.map((job) => agentCard({
    title: job.name || job.id,
    subtitle: job.schedule_human || humanCronSchedule(job.schedule),
    status: job.status,
    kind: 'cron',
    meta: [job.next_run ? `Next ${formatDateTime(job.next_run)}` : '', job.last_run ? `Last ${formatDateTime(job.last_run)}` : ''],
    actions: `<button class="button secondary" data-cron-output="${escapeHtml(job.id)}" data-cron-profile="${escapeHtml(job.profile || 'dashcraft')}">Details</button><button class="button" data-cron-action="run" data-cron-id="${escapeHtml(job.id)}" data-cron-profile="${escapeHtml(job.profile || 'dashcraft')}">Run now</button>${job.status === 'paused' ? `<button class="button secondary" data-cron-action="resume" data-cron-id="${escapeHtml(job.id)}" data-cron-profile="${escapeHtml(job.profile || 'dashcraft')}">Resume</button>` : `<button class="button danger" data-cron-action="pause" data-cron-id="${escapeHtml(job.id)}" data-cron-profile="${escapeHtml(job.profile || 'dashcraft')}">Pause</button>`}`,
  })).join('');
}

function renderAgentOffices(mc) {
  const inventory = Object.fromEntries((mc.profile_inventory || []).map((item) => [item.profile, item]));
  const profiles = mc.profiles || [];
  const profileIds = profiles.map((p) => p.id);
  const savedOrder = readLocalArray('officeOrder', profileIds);
  const orderedIds = [...savedOrder.filter((id) => profileIds.includes(id)), ...profileIds.filter((id) => !savedOrder.includes(id))];
  const orderedProfiles = orderedIds.map((id) => profiles.find((p) => p.id === id)).filter(Boolean);
  return orderedProfiles.map((profile) => `<section class="panel mission-panel agent-office" draggable="true" data-office-id="${escapeHtml(profile.id)}">
    <div class="panel-head manager-head"><div><p class="eyebrow">Main agent office</p><h2>${escapeHtml(profile.name)}</h2><p class="section-copy">This office shows the main Discord-facing/profile agent first, then the sub-agents and cron jobs living under it.</p></div></div>
    <div class="office-layout">
      <div><p class="eyebrow">Main agent</p>${agentOfficeProfileCard(profile, inventory[profile.id])}</div>
      <div><p class="eyebrow">Sub-agents</p><div class="mission-grid nested-grid">${renderOfficeSubagents(profile.id, mc)}</div></div>
      <div><p class="eyebrow">Cron jobs</p><div class="mission-grid nested-grid job-grid">${renderOfficeCronJobs(profile.id, mc)}</div></div>
    </div>
  </section>`).join('') || '<section class="panel"><div class="empty">No Hermes profiles discovered.</div></section>';
}

function renderCronJobs(mc) {
  return (mc.cron_jobs || []).map((job) => agentCard({
    title: job.name || job.id,
    subtitle: job.schedule_human || humanCronSchedule(job.schedule),
    status: job.status,
    kind: 'cron',
    meta: [job.next_run ? `Next ${formatDateTime(job.next_run)}` : '', job.last_run ? `Last ${formatDateTime(job.last_run)}` : ''],
    actions: `<button class="button secondary" data-cron-output="${escapeHtml(job.id)}" data-cron-profile="${escapeHtml(job.profile || 'dashcraft')}">Details</button><button class="button" data-cron-action="run" data-cron-id="${escapeHtml(job.id)}" data-cron-profile="${escapeHtml(job.profile || 'dashcraft')}">Run now</button>${job.status === 'paused' ? `<button class="button secondary" data-cron-action="resume" data-cron-id="${escapeHtml(job.id)}" data-cron-profile="${escapeHtml(job.profile || 'dashcraft')}">Resume</button>` : `<button class="button danger" data-cron-action="pause" data-cron-id="${escapeHtml(job.id)}" data-cron-profile="${escapeHtml(job.profile || 'dashcraft')}">Pause</button>`}<button class="icon-button danger" data-cron-action="remove" data-cron-id="${escapeHtml(job.id)}" data-cron-profile="${escapeHtml(job.profile || 'dashcraft')}" title="Delete cron job">🗑</button>`,
  })).join('') || '<div class="empty">No Hermes cron jobs found. If you expect more, they may belong to another Hermes profile or already completed.</div>';
}



function renderAgentConversations(mc) {
  return (mc.sessions || []).slice(0, 8).map((session) => agentCard({
    title: session.title || session.id,
    subtitle: session.preview || 'recent Hermes conversation',
    status: 'active',
    kind: 'session',
    meta: [session.last_active || 'recent'],
    actions: `<button class="button secondary" data-session-detail="${escapeHtml(session.id)}">Open transcript</button>`,
  })).join('') || '<div class="empty">No recent Hermes sessions found.</div>';
}

function boardStatusClass(name = '') {
  const value = name.toLowerCase();
  if (value.includes('progress')) return 'progress';
  if (value.includes('review')) return 'review';
  if (value.includes('done')) return 'done';
  return 'backlog';
}

function projectLabelHtml(label) {
  const color = (label.color || '').replace(/[^a-fA-F0-9]/g, '').slice(0, 6) || '30363d';
  return `<span class="gh-label" style="--label-color:#${escapeHtml(color)}">${escapeHtml(label.name || '')}</span>`;
}

function projectBoardCard(item) {
  const labels = (item.labels || []).map(projectLabelHtml).join('');
  const assignees = (item.assignees || []).filter(Boolean);
  const assigneeHtml = assignees.length
    ? assignees.map((login) => `<span class="gh-avatar" title="${escapeHtml(login)}">${escapeHtml(fallbackInitial(login))}</span>`).join('')
    : '<span class="gh-avatar muted-avatar" title="Unassigned">—</span>';
  const title = `<strong>${escapeHtml(item.title || 'Untitled GitHub item')}</strong>`;
  return `<a class="gh-board-card" href="${escapeHtml(item.url || '#')}" target="_blank" rel="noreferrer">
    ${title}
    <div class="gh-card-meta"><span>#${escapeHtml(item.number ?? '—')}</span><span>${escapeHtml(item.state || item.status || '')}</span></div>
    ${labels ? `<div class="gh-label-row">${labels}</div>` : ''}
    <div class="gh-card-footer"><span class="gh-state-dot ${item.state === 'CLOSED' ? 'closed' : 'open'}"></span><span>${escapeHtml(item.status || '')}</span><div class="gh-avatars">${assigneeHtml}</div></div>
  </a>`;
}

function projectBoardColumn(column) {
  const items = column.items || [];
  return `<section class="gh-board-column ${boardStatusClass(column.name)}">
    <header><div><span class="gh-column-dot"></span><strong>${escapeHtml(column.name)}</strong></div><span class="gh-count">${column.count ?? items.length}</span></header>
    <div class="gh-board-items">${items.length ? items.map(projectBoardCard).join('') : '<div class="empty compact">No cards in this lane.</div>'}</div>
  </section>`;
}

function renderTradingProject(mc) {
  const project = mc.trading_project || {};
  const issuesUrl = project.issues_url || 'https://github.com/ohjy1006kenneth/AI-Stock-Trader/issues';
  const boardUrl = project.project_url || project.repo_url || 'https://github.com/ohjy1006kenneth/AI-Stock-Trader';
  const columns = project.board_columns || [];
  const boardHtml = columns.length
    ? columns.map(projectBoardColumn).join('')
    : '<div class="empty">GitHub project cards are unavailable. Use Open GitHub board for the source of truth.</div>';
  
  return `<section class="panel trading-panel gh-project-panel">
    <div class="panel-head manager-head"><div><p class="eyebrow">GitHub project office</p><h2>AI Stock Trader</h2><p class="section-copy">Coding-agent work mirrors the GitHub project board: cards move through backlog, implementation, review, and done lanes.</p></div><button class="button secondary" data-refresh-agents="true">Refresh board</button></div>
    <div class="gh-project-toolbar">
      <div class="gh-project-title"><span class="agent-avatar">GH</span><div><strong>${escapeHtml(project.project_title || 'GitHub project board')}</strong><small>${escapeHtml(project.project_status || 'linked')} · ${project.project_items ?? '—'} cards · ${project.open_prs ?? 0} open PRs</small></div></div>
      <div class="gh-project-stats"><span>${project.open_issues ?? '—'} open issues</span><span>${project.closed_issues ?? '—'} closed</span></div>
    </div>
    <div class="gh-board-scroll compact"><div class="gh-board">${boardHtml}</div></div>
    <div class="modal-actions"><a class="button primary" href="${escapeHtml(boardUrl)}" target="_blank" rel="noreferrer">Open GitHub board</a><a class="button secondary" href="${escapeHtml(issuesUrl)}" target="_blank" rel="noreferrer">Manage issues</a></div>
  </section>`;
}

function formatProfileDetail(profileId) {
  const item = missionControl?.profile_inventory?.find((profile) => profile.profile === profileId);
  const profile = missionControl?.profiles?.find((row) => row.id === profileId);
  if (!item && !profile) return `No details for ${profileId}`;
  const logs = (item?.logs || []).map((log) => `- ${log.name}: ${Math.round((log.bytes || 0) / 1024)} KB`).join('\n') || '- No logs found';
  const scripts = (item?.scripts || []).map((script) => `- ${script.name}`).join('\n') || '- No scripts found';
  const memory = (item?.memory_files || []).map((file) => `- ${file.name}: ${Math.round((file.bytes || 0) / 1024)} KB`).join('\n') || '- No memory files found';
  return `${profile?.name || profileId}\nStatus: ${profile?.status || 'unknown'}\nModel: ${profile?.model || 'unknown'}\nHome: ${item?.home || 'unknown'}\nSkills: ${item?.skills_count ?? 0}\n\nScripts\n${scripts}\n\nLogs\n${logs}\n\nMemory/docs\n${memory}`;
}

function renderAgentsPage() {
  const mc = missionControl;
  if (!mc) {
    contentEl.innerHTML = '<section class="panel"><div class="empty">Loading Hermes agents…</div></section>';
    return;
  }
  contentEl.innerHTML = `<section class="panel mission-intro"><div class="panel-head manager-head"><div><p class="eyebrow">Hermes agents</p><h2>Agent offices</h2><p class="section-copy">Main agents are the Hermes profiles/channels you speak to. Each office below groups its sub-agents and cron jobs under that main agent.</p></div><button class="button secondary" data-refresh-agents="true">Refresh</button></div></section>
    ${renderTradingProject(mc)}
    ${renderAgentOffices(mc)}`;
}

function cleanSummaryParagraph(paragraph) {
  const cleaned = String(paragraph || '')
    .trim()
    .replace(/(^|[.!?]\s+)For Kenneth,\s*([a-z])/gi, (_match, prefix, first) => `${prefix}${first.toUpperCase()}`)
    .replace(/\bFor Kenneth,\s*/gi, '')
    .replace(/\s+/g, ' ');
  return cleaned ? cleaned.charAt(0).toUpperCase() + cleaned.slice(1) : '';
}

function paragraphize(text, maxParagraphs = 1) {
  const paragraphs = String(text || '')
    .split(/\n\s*\n/)
    .map(cleanSummaryParagraph)
    .filter(Boolean)
    .slice(0, maxParagraphs);
  return paragraphs.map((paragraph) => `<p>${escapeHtml(paragraph)}</p>`).join('');
}

function curationHtml() {
  const items = dailyCuration?.items || [];
  if (!items.length) return '<div class="empty compact">Fetch news to build today\'s Semiconductor / Stocks / AI curation.</div>';
  return `<section class="curation-grid">${items.map((item) => `<a class="curation-card" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer"><span class="badge">${escapeHtml(item.category)}</span><strong>${escapeHtml(item.title)}</strong><div class="curation-summary">${paragraphize(item.summary || '', 2)}</div><small>${escapeHtml(item.source)} · ${formatDateOnly(item.published_ts || item.published_at || '')}</small></a>`).join('')}</section>`;
}

function renderNewsPage() {
  const tabs = ['All', ...newsletterSources.map((s) => s.name)];
  const briefGenerated = dailyCuration?.generated_at ? `Generated ${formatDateTime ? formatDateTime(dailyCuration.generated_at) : new Date(dailyCuration.generated_at).toLocaleString()}` : 'Builds after fetch';
  const shownItems = visibleNewsItems(newsletters);
  const totalShown = shownItems.length;
  const canLoadMore = newsletters.length === newsVisibleLimit && newsletters.length < NEWS_MAX_VISIBLE;
  const atMax = totalShown >= NEWS_MAX_VISIBLE;
  contentEl.innerHTML = `<section class="panel daily-curation">
    <div class="panel-head"><div><p class="eyebrow">Daily brief · ${escapeHtml(dailyCuration?.source || 'news-brief')}</p><h2>${escapeHtml(dailyCuration?.headline || '3 picks for you')}</h2><p class="section-copy">Two-paragraph summaries for today's Semiconductor, Stocks, and AI picks. ${escapeHtml(briefGenerated)}.</p></div></div>
    ${curationHtml()}
  </section>
  <section class="panel">
    <div class="panel-head manager-head"><div><p class="eyebrow">Latest News</p><h2>RSS briefings</h2><p class="section-copy">Showing ${totalShown} item${totalShown === 1 ? '' : 's'}${newsSourceFilter ? ` from ${escapeHtml(newsSourceFilter)}` : ''}. Use Load more when you want extra history.</p></div><div class="manager-filters"><button class="button primary" data-fetch-news="true">Fetch Now</button><button class="button secondary" data-refresh-news="true">Refresh</button></div></div>
    <div class="tab-row news-tabs" role="tablist" aria-label="Filter news by source">${tabs.map((source) => {
      const value = source === 'All' ? '' : source;
      const active = value === newsSourceFilter;
      return `<button type="button" role="tab" aria-selected="${active ? 'true' : 'false'}" class="tab-button ${active ? 'active' : ''}" data-news-source="${escapeHtml(value)}">${escapeHtml(source)}</button>`;
    }).join('')}</div>
    <div id="news-list" class="news-list">${newsItemsHtml(shownItems)}</div>
    <div class="news-footer-actions">${canLoadMore ? `<button class="button secondary" data-load-more-news="true">Load ${NEWS_PAGE_SIZE} more <span class="muted">(${totalShown}/${NEWS_MAX_VISIBLE})</span></button>` : atMax ? '<span class="muted">Showing all loaded articles (max 60).</span>' : newsSourceFilter ? '<span class="muted">End of this source feed.</span>' : '<span class="muted">End of currently loaded feed.</span>'}</div>
  </section>`;
}

function newsItemsHtml(items) {
  return items.length ? items.map((item, index) => `<a class="news-card" data-news-index="${index}" href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer"><div><span class="badge">${escapeHtml(item.source)}</span><strong>${escapeHtml(item.title)}</strong></div><div class="curation-summary">${paragraphize(item.summary || '', 1)}</div><footer><small>${formatDateOnly(item.published_ts || item.published_at || '')}</small><small>Open article →</small></footer></a>`).join('') : '<div class="empty">No newsletter items yet. Click Fetch Now.</div>';
}

function percentLabel(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : '—';
}

function auditBadge(status = 'warn') {
  const normalized = ['pass', 'warn', 'fail'].includes(status) ? status : 'warn';
  return `<span class="audit-badge ${normalized}">${escapeHtml(normalized.toUpperCase())}</span>`;
}

function sectionEmpty(items = []) {
  return items.length ? '' : '<div class="empty compact">No rows are available for this section.</div>';
}

function friendlyStockWarning(message) {
  const text = String(message || '').trim();
  if (!text) return '';
  return text
    .replace(/Layer 1/gi, 'Layer 1')
    .replace(/per-ticker history file is missing\.?/i, 'has no saved Layer 1 history file yet.')
    .replace(/No Layer 1 history rows fell inside the selected date window\.?/i, 'has saved Layer 1 data, but none of it falls inside this selected date window.')
    .replace(/source data is not available/i, 'source data is not available for this selected window')
    .replace(/subprocess failed/i, 'AI-Stock-Trader could not finish building this review packet');
}

function stockReadinessIssueItems({ rowsLoaded, loadWarnings = [], reviewBlockers = [], semanticTotal = 0 }) {
  const issues = [];
  if (rowsLoaded === 0) {
    issues.push('No rows were loaded for the selected window, so there is nothing safe to review yet.');
  } else if (semanticTotal === 0) {
    issues.push('Rows were found, but the semantic evidence cards are empty. The review would only show raw counts, not explanations.');
  }
  reviewBlockers.forEach((item) => issues.push(friendlyStockWarning(item)));
  loadWarnings.forEach((item) => issues.push(friendlyStockWarning(item)));
  return [...new Set(issues.filter(Boolean))];
}

function stockReadinessIssueHtml(issues = [], fallback = 'No blocking data issues were returned by the backend.') {
  if (!issues.length) return `<div class="empty compact">${escapeHtml(fallback)}</div>`;
  return `<ul class="audit-warning-list">${issues.map((issue) => `<li>${escapeHtml(issue)}</li>`).join('')}</ul>`;
}

function pctLabel(value) {
  const n = Number(value);
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : '—';
}

function stockPacketCandidates() {
  const options = stockReviewOptions || stockAudit?.review_options || stockAudit || {};
  return Array.isArray(options.candidates) ? options.candidates : [];
}

function selectedStockPacket() {
  const candidates = stockPacketCandidates();
  if (!candidates.length) {
    const fallback = stockAudit?.selected_review || stockAudit?.default_selection || stockReviewOptions?.default_selection || {};
    return fallback && typeof fallback === 'object' ? fallback : {};
  }
  return candidates.find((candidate) => candidate.id === stockSelectedReviewId) || stockReviewOptions?.default_selection || candidates[0] || {};
}

function ensureStockSelectionFromOptions(options = stockReviewOptions || stockAudit || {}) {
  const candidates = Array.isArray(options.candidates) ? options.candidates : [];
  const defaultPacket = options.default_selection || candidates[0] || {};
  if (!stockSelectedReviewId && defaultPacket.id) stockSelectedReviewId = defaultPacket.id;
  const packet = selectedStockPacket();
  const tickers = Array.isArray(packet.tickers) ? packet.tickers.map((item) => String(item).toUpperCase()) : [];
  if (!stockSelectedTicker || !tickers.includes(stockSelectedTicker)) {
    stockSelectedTicker = tickers.includes('AAPL') ? 'AAPL' : (tickers[0] || 'AAPL');
  }
}

function stockAuditUrl() {
  const packet = selectedStockPacket();
  const params = new URLSearchParams();
  const fromDate = packet.from_date || stockAudit?.query?.from_date;
  const toDate = packet.to_date || stockAudit?.query?.to_date || fromDate;
  if (fromDate) params.set('from_date', fromDate);
  if (toDate) params.set('to_date', toDate);
  if (stockSelectedTicker) params.set('tickers', stockSelectedTicker);
  const query = params.toString();
  return `/api/stocks/audit${query ? `?${query}` : ''}`;
}

function rowTicker(row) {
  return String(row?.ticker || row?.symbol || row?.company_ticker || '').toUpperCase();
}

function rowsForTicker(rows, ticker = stockSelectedTicker) {
  if (!Array.isArray(rows)) return [];
  const normalized = String(ticker || '').toUpperCase();
  if (!normalized) return rows;
  return rows.filter((row) => rowTicker(row) === normalized || !rowTicker(row));
}

function benchmarkTicker(value) {
  const ticker = String(value || '').toUpperCase();
  return ['SPY', 'SPX', '^GSPC', 'GSPC', 'SP500', 'S&P500', 'MARKET'].includes(ticker);
}

function scoreValue(...values) {
  for (const value of values) {
    const n = Number(value);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

function probabilityCell(row, keys) {
  const value = scoreValue(...keys.map((key) => row?.[key]));
  return value === null ? '—' : pctLabel(value > 1 ? value / 100 : value);
}

function blockerBox(title, copy) {
  return `<div class="not-reviewable-box"><strong>${escapeHtml(title)}</strong><p>${escapeHtml(copy)}</p></div>`;
}

function stockAuditHtml() {
  if (!stockAudit) {
    return '<section class="panel audit-panel"><div class="panel-head"><div><p class="eyebrow">AI Stock Trader</p><h2>NLP review</h2><p class="section-copy">Loading ticker-selectable NLP review options…</p></div><button class="button secondary" data-refresh-stock-audit="true">Refresh</button></div><div class="empty">Loading Stocks review data…</div></section>';
  }
  if (!stockAudit.ok) {
    const reason = friendlyStockWarning(stockAudit.reason || 'Audit data unavailable.');
    return `<section class="panel review-ready-panel fail">
      <div class="panel-head manager-head"><div><p class="eyebrow">AI Stock Trader · review readiness</p><h2>Not reviewable</h2><p class="section-copy">The dashboard could not load an AI-Stock-Trader review packet, so there is nothing safe to inspect yet.</p></div><button class="button secondary" data-refresh-stock-audit="true">Retry</button></div>
      <div class="readiness-explainer"><article><strong>Why this is not reviewable</strong><p>${escapeHtml(reason)}</p></article><article><strong>Recommended next step</strong><p>Check that AI-Stock-Trader is available, then refresh after a review packet can be built.</p></article></div>
      <details class="advanced-diagnostics"><summary>Advanced diagnostics</summary><pre>${escapeHtml(JSON.stringify({ status: stockAudit.status, reason: stockAudit.reason, error: stockAudit.error }, null, 2))}</pre></details>
    </section>`;
  }

  ensureStockSelectionFromOptions(stockReviewOptions || stockAudit.review_options || stockAudit);
  const payload = stockAudit.payload || {};
  const report = payload.report || {};
  const packet = selectedStockPacket();
  const packetTickers = Array.isArray(packet.tickers) && packet.tickers.length ? packet.tickers.map((item) => String(item).toUpperCase()) : (Array.isArray(report.tickers) ? report.tickers.map((item) => String(item).toUpperCase()) : []);
  const selectedTicker = stockSelectedTicker || (packetTickers.includes('AAPL') ? 'AAPL' : packetTickers[0] || 'AAPL');
  const start = packet.from_date || report.from_date || stockAudit.query?.from_date || '—';
  const end = packet.to_date || report.to_date || stockAudit.query?.to_date || start;
  const candidates = stockPacketCandidates();
  const counts = stockAudit.review_counts || packet.counts || {};
  const rowsLoaded = Number(stockAudit.rows_loaded ?? report.rows_loaded ?? (payload.report ? 0 : packet.rows_loaded) ?? 0) || 0;
  const loadWarnings = Array.isArray(stockAudit.warnings) ? stockAudit.warnings : (report.load_warnings || []).map((item) => item?.ticker && item?.message ? `${item.ticker}: ${item.message}` : String(item?.message || item || '')).filter(Boolean);
  const pilot = payload.pilot_window_analysis || {};
  const evidence = payload.pilot_window_evidence || {};
  const sentimentEvidence = evidence.sentiment || {};
  const topicEvidence = evidence.topics || {};
  const regimeEvidence = evidence.regime || {};
  const finbertRowsAll = Array.isArray(sentimentEvidence.rows) && sentimentEvidence.rows.length ? sentimentEvidence.rows : (Array.isArray(pilot.finbert?.rows) ? pilot.finbert.rows : []);
  const sentenceRowsAll = Array.isArray(sentimentEvidence.rows) ? sentimentEvidence.rows : [];
  const topicRowsAll = Array.isArray(topicEvidence.rows) ? topicEvidence.rows : [];
  const topicFeatureRowsAll = Array.isArray(topicEvidence.feature_rows) ? topicEvidence.feature_rows : [];
  const hmmRowsAll = Array.isArray(pilot.hmm?.rows) ? pilot.hmm.rows : [];
  const regimeRowsAll = Array.isArray(regimeEvidence.rows) ? regimeEvidence.rows : [];
  const finbertRows = rowsForTicker(finbertRowsAll, selectedTicker);
  const sentenceRows = rowsForTicker(sentenceRowsAll, selectedTicker);
  const topicRows = rowsForTicker(topicRowsAll, selectedTicker);
  const topicFeatureRows = rowsForTicker(topicFeatureRowsAll, selectedTicker);
  const benchmarkRows = regimeRowsAll.length ? regimeRowsAll : hmmRowsAll.filter((row) => benchmarkTicker(row.ticker || row.market_proxy));
  const benchmarkPriceRows = benchmarkRows.filter((row) => scoreValue(row.close, row.price, row.spy_close, row.sp500_close, row.benchmark_close) !== null);
  const tickerHmmRows = rowsForTicker(hmmRowsAll, selectedTicker);
  const heatmapRows = Array.isArray(payload.heatmap?.rows) ? payload.heatmap.rows : [];
  const heatmapColumns = Array.isArray(payload.heatmap?.columns) ? payload.heatmap.columns : [];
  const outliers = payload.outliers && Array.isArray(payload.outliers.points) ? payload.outliers.points : [];
  const nulls = payload.null_rates || {};
  const featureNulls = Array.isArray(nulls.by_feature) ? nulls.by_feature : [];
  const familyNulls = Array.isArray(nulls.by_family) ? nulls.by_family : [];
  const spotChecks = payload.spot_checks || {};
  const spotCheckFeatures = Array.isArray(spotChecks.feature_options) ? spotChecks.feature_options : [];
  const spotSeries = Array.isArray(spotChecks.series) ? rowsForTicker(spotChecks.series, selectedTicker) : [];
  const formulaCards = Array.isArray(payload.formula_cards) ? payload.formula_cards : [];
  const embeddedRegimeRows = Number(counts.embedded_hmm_rows ?? stockAudit.hmm_rows ?? tickerHmmRows.length ?? 0) || 0;
  const embeddingMeta = payload.embeddings || payload.embedding || pilot.embeddings || pilot.embedding || (topicRows.length || topicFeatureRows.length ? {
    model: topicRows[0]?.topic_model || topicFeatureRows[0]?.topic_model || 'BERTopic',
    model_version: topicRows[0]?.topic_model_version || topicFeatureRows[0]?.topic_model_version || 'available',
    article_count: topicRows.length,
    chunk_count: topicRows.length,
    topic_linkage_count: topicFeatureRows.length,
    embedding_cache_key: topicRows[0]?.embedding_cache_key || topicFeatureRows[0]?.embedding_cache_key || '',
    topic_feature_key: topicFeatureRows[0]?.topic_feature_key || '',
  } : null);
  const hasSentenceFinbert = sentenceRows.length && finbertRows.some((row) => row.sentence_text || row.chunk_text || row.text || row.headline);
  const articleCount = new Set(sentenceRows.map((row) => row.article_id || row.url || row.headline || row.title).filter(Boolean).map(String)).size;
  const hasAggregateFinbert = finbertRows.length || Number(counts.finbert_rows || stockAudit.finbert_rows || 0) > 0;
  const hasTopicEvidence = topicRows.length || topicFeatureRows.length || Number(counts.topic_rows || stockAudit.topic_rows || 0) > 0;
  const hasBenchmarkEvidence = benchmarkRows.length > 0;
  const hasBenchmarkGraph = benchmarkPriceRows.length > 0;
  const selectedBlockers = [];
  if (!hasSentenceFinbert) selectedBlockers.push('FinBERT sentence/chunk evidence is missing');
  if (!embeddingMeta) selectedBlockers.push('Embeddings metadata/coverage is missing');
  if (!hasTopicEvidence) selectedBlockers.push('BERTopic topic/relevance evidence is missing');
  if (!hasBenchmarkEvidence) selectedBlockers.push('SPY/S&P benchmark HMM evidence is missing');
  else if (!hasBenchmarkGraph) selectedBlockers.push('SPY/S&P benchmark price graph is missing, but HMM probabilities are available');
  const reviewStatus = selectedBlockers.length ? 'Needs NLP evidence' : 'Ready to review';
  const reviewStatusClass = selectedBlockers.length ? 'warn' : 'pass';
  const packetOptions = candidates.length ? candidates.map((candidate) => `<option value="${escapeHtml(candidate.id || '')}" ${candidate.id === packet.id ? 'selected' : ''}>${escapeHtml(candidate.label || candidate.id || 'Review packet')}</option>`).join('') : `<option>${escapeHtml(packet.label || `${start} · ${packetTickers.join(', ')}`)}</option>`;
  const tickerOptions = packetTickers.length ? packetTickers.map((ticker) => `<option value="${escapeHtml(ticker)}" ${ticker === selectedTicker ? 'selected' : ''}>${escapeHtml(ticker)}</option>`).join('') : `<option value="${escapeHtml(selectedTicker)}">${escapeHtml(selectedTicker)}</option>`;
  const packetCopy = packetTickers.length ? `This packet includes ${packetTickers.join(', ')}; choose one ticker to inspect.` : 'Choose one ticker to inspect when packet tickers are available.';
  const controlsHtml = `<section class="panel stock-control-panel">
    <div class="panel-head manager-head"><div><p class="eyebrow">Packet controls</p><h2>${escapeHtml(selectedTicker)} NLP review</h2><p class="section-copy">${escapeHtml(packetCopy)}</p></div><button class="button secondary" data-refresh-stock-audit="true">Refresh</button></div>
    <div class="stock-control-grid"><label>Packet/date<select data-stock-packet>${packetOptions}</select></label><label>Ticker<select data-stock-ticker>${tickerOptions}</select></label><div class="review-ready-meta"><span>Window: ${escapeHtml(start)} → ${escapeHtml(end)}</span><span>Rows loaded: ${rowsLoaded || Number(packet.rows_loaded || 0)}</span><span>Selected: ${escapeHtml(selectedTicker)}</span></div></div>
  </section>`;
  const readinessPanel = `<section class="panel review-ready-panel ${reviewStatusClass}">
    <div class="panel-head manager-head"><div><p class="eyebrow">Selected ticker reviewability</p><h2>${escapeHtml(reviewStatus)}</h2><p class="section-copy">${escapeHtml(selectedBlockers.length ? `${selectedTicker} has aggregate Layer 1 rows, but the requested per-ticker NLP review is still partial: ${selectedBlockers.join(', ')}.` : `${selectedTicker} has the sentence, embedding, topic, and market benchmark evidence requested for review.`)}</p></div></div>
    <div class="review-ready-meta"><span>FinBERT chunks: ${sentenceRows.length}</span><span>Articles: ${articleCount}</span><span>FinBERT aggregates: ${finbertRows.length || Number(counts.finbert_rows || 0)}</span><span>Embeddings: ${embeddingMeta ? 'metadata present' : 'blocked'}</span><span>BERTopic: ${topicRows.length}</span><span>SPY/S&P graph: ${hasBenchmarkGraph ? 'present' : 'blocked'}</span></div>
    ${selectedBlockers.length ? stockReadinessIssueHtml(selectedBlockers) : '<div class="empty compact">All selected-ticker NLP review evidence is present.</div>'}
  </section>`;

  const overviewHtml = `<details class="nlp-section" open><summary>1. Overview / what can I review?</summary><div class="review-card-grid">
    <article class="review-card ${hasSentenceFinbert ? 'ready' : hasAggregateFinbert ? 'needs-attention' : 'blocker'}"><div><p class="eyebrow">FinBERT</p><h3>Sentence chunks</h3></div>${auditBadge(hasSentenceFinbert ? 'pass' : hasAggregateFinbert ? 'warn' : 'fail')}<strong>${hasSentenceFinbert ? sentenceRows.length : finbertRows.length || Number(counts.finbert_rows || 0)}</strong><p>${escapeHtml(hasSentenceFinbert ? `${articleCount} article${articleCount === 1 ? '' : 's'} available for human text review.` : hasAggregateFinbert ? 'Only ticker-day aggregate FinBERT is available; sentence/chunk evidence is missing.' : 'No FinBERT evidence is available for the selected ticker.')}</p></article>
    <article class="review-card ${embeddingMeta ? 'ready' : 'blocker'}"><div><p class="eyebrow">Embeddings</p><h3>Coverage metadata</h3></div>${auditBadge(embeddingMeta ? 'pass' : 'fail')}<strong>${embeddingMeta ? '1' : '0'}</strong><p>${escapeHtml(embeddingMeta ? 'Embedding coverage metadata is present; raw vectors are intentionally not shown.' : 'Blocked: embedding model/version and article/chunk coverage are unavailable.')}</p></article>
    <article class="review-card ${hasTopicEvidence ? 'ready' : 'blocker'}"><div><p class="eyebrow">BERTopic</p><h3>Topics</h3></div>${auditBadge(hasTopicEvidence ? 'pass' : 'fail')}<strong>${topicRows.length}</strong><p>${escapeHtml(hasTopicEvidence ? 'Topic rows are available for selected ticker review.' : 'Blocked: topic labels, probabilities, examples, and relevance explanations are missing.')}</p></article>
    <article class="review-card ${hasBenchmarkEvidence ? 'ready' : 'blocker'}"><div><p class="eyebrow">HMM</p><h3>S&P 500/SPY graph</h3></div>${auditBadge(hasBenchmarkEvidence ? 'pass' : 'fail')}<strong>${benchmarkRows.length || embeddedRegimeRows}</strong><p>${escapeHtml(hasBenchmarkGraph ? 'Benchmark price rows and HMM regime probabilities are available.' : hasBenchmarkEvidence ? 'HMM regime probabilities are available, but the benchmark price graph is still missing.' : 'Blocked: SPY/S&P benchmark price rows are missing; ticker-level copied regime rows are not shown as a substitute.')}</p></article>
  </div></details>`;

  const finbertHtml = () => {
    if (hasSentenceFinbert) {
      const articleGroups = [];
      const byArticle = new Map();
      finbertRows.forEach((row) => {
        const key = String(row.article_id || row.url || row.headline || row.title || row.source || 'unknown');
        if (!byArticle.has(key)) byArticle.set(key, { row, rows: [] });
        byArticle.get(key).rows.push(row);
      });
      byArticle.forEach((value) => articleGroups.push(value));
      return `<div class="review-ready-meta"><span>${articleGroups.length} articles</span><span>${sentenceRows.length} sentence chunks</span><span>Grouped by article so repeated headlines are expected inside each article.</span></div>${articleGroups.map((group, index) => {
        const first = group.row || {};
        const headline = first.headline || first.title || first.article_title || first.source || 'Article';
        const sourceLine = [first.source || first.publisher, first.published_at || first.date].filter(Boolean).join(' · ');
        return `<details class="advanced-diagnostics" ${index < 3 ? 'open' : ''}><summary>${reviewTextHtml(headline)} <small>${group.rows.length} chunk${group.rows.length === 1 ? '' : 's'}${sourceLine ? ` · ${reviewTextHtml(sourceLine)}` : ''}</small></summary><div class="audit-table-wrap"><table class="audit-table"><thead><tr><th>#</th><th>Sentence/chunk</th><th>Positive</th><th>Neutral</th><th>Negative</th><th>Score</th><th>Ticker relevance</th></tr></thead><tbody>${group.rows.map((row) => `<tr><td>${escapeHtml(row.sentence_index ?? '—')}</td><td>${reviewTextHtml(row.sentence_text || row.chunk_text || row.text || row.headline || '—')}</td><td>${probabilityCell(row, ['positive_probability', 'positive_prob', 'prob_positive', 'sentiment_positive'])}</td><td>${probabilityCell(row, ['neutral_probability', 'neutral_prob', 'prob_neutral', 'sentiment_neutral'])}</td><td>${probabilityCell(row, ['negative_probability', 'negative_prob', 'prob_negative', 'sentiment_negative'])}</td><td>${escapeHtml(row.sentiment_score ?? row.score ?? '—')}</td><td>${reviewTextHtml(row.relevance_reason || row.relevance_evidence || row.topic_evidence || '—')} ${probabilityCell(row, ['relevance_score'])}</td></tr>`).join('')}</tbody></table></div></details>`;
      }).join('')}`;
    }
    if (hasAggregateFinbert) {
      return `${blockerBox('Only ticker-day aggregate FinBERT is available; sentence/chunk evidence is missing.', 'These aggregate values are useful as a data-presence check, but they are not enough for Kenneth’s requested per-sentence NLP review.')}<div class="audit-table-wrap"><table class="audit-table"><thead><tr><th>Date</th><th>Ticker</th><th>Status</th><th>Score</th><th>Articles</th><th>Sentences</th><th>Relevance</th></tr></thead><tbody>${finbertRows.map((row) => `<tr><td>${escapeHtml(row.date || '—')}</td><td>${escapeHtml(row.ticker || selectedTicker)}</td><td>${auditBadge(row.status || 'warn')}</td><td>${escapeHtml(row.sentiment_score ?? '—')}</td><td>${escapeHtml(row.article_count ?? '—')}</td><td>${escapeHtml(row.sentence_count ?? '—')}</td><td>${probabilityCell(row, ['relevance_score'])}</td></tr>`).join('') || `<tr><td>${escapeHtml(end)}</td><td>${escapeHtml(selectedTicker)}</td><td>${auditBadge('warn')}</td><td colspan="4">Aggregate count reported by review options, but detailed rows have not loaded.</td></tr>`}</tbody></table></div>`;
    }
    return blockerBox('FinBERT evidence is missing.', 'No sentence/chunk rows or aggregate ticker-day FinBERT rows are available for the selected ticker.');
  };

  const embeddingsHtml = () => {
    if (!embeddingMeta) return blockerBox('Embeddings are unavailable.', 'The audit payload does not include embedding model name/version, embedded article/chunk counts, or topic/relevance linkage for the selected ticker. Raw vectors are not human-readable and should not be shown here.');
    const meta = embeddingMeta || {};
    return `<div class="readiness-explainer"><article><strong>Model</strong><p>${escapeHtml(meta.model || meta.model_name || meta.version || 'Embedding model metadata present')}</p></article><article><strong>Coverage</strong><p>${escapeHtml(`${meta.article_count ?? meta.embedded_article_count ?? '—'} articles · ${meta.chunk_count ?? meta.embedded_chunk_count ?? '—'} chunks · ${meta.topic_linkage_count ?? '—'} topic links`)}</p></article></div><div class="empty compact">Vectors are intentionally hidden because they are not human-readable; review coverage and linkage metadata instead.</div>`;
  };

  const topicHtml = () => {
    if (!topicRows.length) return blockerBox('BERTopic topics are unavailable.', 'The selected ticker has no topic ID, label/keywords, probability, examples/articles/sentences, or relevance explanation in this audit payload.');
    return `<div class="audit-table-wrap"><table class="audit-table"><thead><tr><th>Date</th><th>Topic ID</th><th>Label / keywords</th><th>Probability</th><th>Examples / evidence</th><th>Relevance</th></tr></thead><tbody>${topicRows.map((row) => {
      const topicLabel = row.topic_label || row.label || row.keywords || [row.topic_model, row.topic_model_version].filter(Boolean).join(' · ') || `Topic ${row.topic_id ?? '—'}`;
      const exampleText = row.example || row.examples || row.topic_evidence || row.sentence_text || row.text || '—';
      const relevanceText = row.relevance_reason || row.relevance_evidence || row.embedding_cache_key || '';
      return `<tr><td>${escapeHtml(row.date || '—')}</td><td>${escapeHtml(row.topic_id ?? row.topic ?? '—')}</td><td>${reviewTextHtml(topicLabel)}</td><td>${probabilityCell(row, ['topic_probability', 'probability', 'topic_prob'])}</td><td>${reviewTextHtml(exampleText)}</td><td>${reviewTextHtml(relevanceText)} ${probabilityCell(row, ['relevance_score'])}</td></tr>`;
    }).join('')}</tbody></table></div>`;
  };

  const hmmHtml = () => {
    if (!hasBenchmarkEvidence) return `${blockerBox('SPY/S&P benchmark graph is blocked.', `The payload has ${embeddedRegimeRows || tickerHmmRows.length || 0} ticker-level/embedded HMM row(s), but no aligned SPY/S&P benchmark evidence. The dashboard does not show copied ticker regime rows as a substitute.`)}`;
    if (!hasBenchmarkGraph) {
      return `${blockerBox('SPY/S&P benchmark price graph is blocked.', `The payload has ${benchmarkRows.length} HMM regime row(s), but no price series was supplied for the graph. The HMM probabilities are still shown below.`)}<div class="audit-table-wrap"><table class="audit-table"><thead><tr><th>Date</th><th>Market proxy</th><th>Regime</th><th>Bear</th><th>Sideways</th><th>Bull</th></tr></thead><tbody>${benchmarkRows.map((row) => `<tr><td>${escapeHtml(row.date || '—')}</td><td>${escapeHtml(row.ticker || row.market_proxy || 'SPY/S&P')}</td><td>${escapeHtml(row.regime_label || '—')}</td><td>${probabilityCell(row, ['regime_prob_bear', 'bear_probability'])}</td><td>${probabilityCell(row, ['regime_prob_sideways', 'sideways_probability'])}</td><td>${probabilityCell(row, ['regime_prob_bull', 'bull_probability'])}</td></tr>`).join('')}</tbody></table></div>`;
    }
    const prices = benchmarkPriceRows.map((row) => scoreValue(row.close, row.price, row.spy_close, row.sp500_close, row.benchmark_close)).filter((value) => value !== null);
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const points = benchmarkPriceRows.map((row, index) => {
      const price = scoreValue(row.close, row.price, row.spy_close, row.sp500_close, row.benchmark_close);
      const x = benchmarkPriceRows.length <= 1 ? 0 : (index / (benchmarkPriceRows.length - 1)) * 100;
      const y = max === min ? 50 : 90 - ((price - min) / (max - min)) * 80;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' ');
    return `<div class="benchmark-chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-label="SPY/S&P price line"><polyline points="${points}" /></svg><div class="review-ready-meta"><span>Min ${min.toFixed(2)}</span><span>Max ${max.toFixed(2)}</span><span>Rows ${benchmarkPriceRows.length}</span></div></div><div class="audit-table-wrap"><table class="audit-table"><thead><tr><th>Date</th><th>Market proxy</th><th>Regime</th><th>Bear</th><th>Sideways</th><th>Bull</th></tr></thead><tbody>${benchmarkRows.map((row) => `<tr><td>${escapeHtml(row.date || '—')}</td><td>${escapeHtml(row.ticker || row.market_proxy || 'SPY/S&P')}</td><td>${escapeHtml(row.regime_label || '—')}</td><td>${probabilityCell(row, ['regime_prob_bear', 'bear_probability'])}</td><td>${probabilityCell(row, ['regime_prob_sideways', 'sideways_probability'])}</td><td>${probabilityCell(row, ['regime_prob_bull', 'bull_probability'])}</td></tr>`).join('')}</tbody></table></div>`;
  };

  const advancedHtml = `<details class="nlp-section"><summary>6. Advanced diagnostics</summary>
    <details class="advanced-diagnostics"><summary>Final features / heatmap / spot checks</summary>
      ${heatmapRows.length && heatmapColumns.length ? `<div class="audit-table-wrap"><table class="audit-table heatmap-table sticky-first-col"><thead><tr><th>Feature</th>${heatmapColumns.map((column) => `<th>${escapeHtml(column.label || column.row_key || '')}</th>`).join('')}</tr></thead><tbody>${heatmapRows.slice(0, 40).map((row) => `<tr><th scope="row">${escapeHtml(row.feature_name || row.family || 'Feature')}</th>${(row.cells || []).map((cell) => `<td class="${escapeHtml(cell.status || '')}" title="${escapeHtml(cell.message || '')}">${escapeHtml(cell.value_label ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : '<div class="empty compact">No feature heatmap rows available.</div>'}
      <div class="audit-list">${spotCheckFeatures.slice(0, 20).map((item) => `<div class="audit-row"><span>${escapeHtml(item.feature_name || 'Feature')}</span>${auditBadge(item.fail_count ? 'fail' : item.warn_count ? 'warn' : 'pass')}<small>${escapeHtml(selectedTicker)} · ${escapeHtml(item.pass_count ?? 0)} pass · ${escapeHtml(item.warn_count ?? 0)} warn · ${escapeHtml(item.fail_count ?? 0)} fail</small></div>`).join('') || '<div class="empty compact">No spot checks available.</div>'}</div>
    </details>
    <details class="advanced-diagnostics"><summary>Warnings, formulas, outliers, null rates, raw counts</summary><div class="advanced-diagnostics-grid"><article><h3>Warnings</h3>${stockReadinessIssueHtml(loadWarnings, 'No warnings returned.')}</article><article><h3>Outliers / formulas</h3><div class="audit-list">${[...outliers.slice(0, 20), ...formulaCards.filter((card) => card.status === 'fail' || card.status === 'warn').slice(0, 20)].map((item) => `<div class="audit-row"><span>${escapeHtml(item.title || item.feature_name || item.rule_type || 'Diagnostic')}</span>${auditBadge(item.status || item.severity ? 'warn' : 'pass')}<small>${escapeHtml(item.message || item.missing_reason || item.date || '')}</small></div>`).join('') || '<div class="empty compact">No outliers or formula issues.</div>'}</div></article><article><h3>Null rates by feature</h3><div class="audit-list">${featureNulls.slice(0, 30).map((item) => `<div class="audit-row"><span>${escapeHtml(item.feature_name || item.family || '—')}</span><small>missing ${pctLabel(item.missing_rate)} · null ${pctLabel(item.null_rate)} · invalid ${pctLabel(item.invalid_rate)}</small></div>`).join('') || '<div class="empty compact">No null-rate rows.</div>'}</div></article><article><h3>Null rates by family</h3><div class="audit-list">${familyNulls.map((item) => `<div class="audit-row"><span>${escapeHtml(item.family || '—')}</span>${auditBadge(item.status || 'warn')}<small>missing ${pctLabel(item.missing_rate)} · null ${pctLabel(item.null_rate)} · invalid ${pctLabel(item.invalid_rate)}</small></div>`).join('') || '<div class="empty compact">No family null-rate rows.</div>'}</div></article></div><pre>${escapeHtml(JSON.stringify({ status: stockAudit.status, query: stockAudit.query, selected_packet: packet, selected_ticker: selectedTicker, counts, blockers: selectedBlockers }, null, 2))}</pre></details>
  </details>`;

  return `${controlsHtml}${readinessPanel}<section class="panel audit-panel nlp-review-flow">
    <div class="panel-head manager-head"><div><p class="eyebrow">Focused NLP review</p><h2>${escapeHtml(selectedTicker)} evidence tabs</h2><p class="section-copy">Primary flow is limited to selected-ticker NLP evidence. Aggregate feature checks and raw audit rows are collapsed under Advanced diagnostics.</p></div></div>
    ${overviewHtml}
    <details class="nlp-section" open><summary>2. FinBERT sentence chunks</summary>${finbertHtml()}</details>
    <details class="nlp-section"><summary>3. Embeddings</summary>${embeddingsHtml()}</details>
    <details class="nlp-section"><summary>4. BERTopic topics</summary>${topicHtml()}</details>
    <details class="nlp-section"><summary>5. HMM + S&P 500/SPY</summary>${hmmHtml()}</details>
    ${advancedHtml}
  </section>`;
}

function renderStocksPage() {
  contentEl.innerHTML = stockAuditHtml();
}

function optionSelected(current, value) {
  return String(current) === String(value) ? 'selected' : '';
}

function checkboxChecked(values, value) {
  return Array.isArray(values) && values.includes(value) ? 'checked' : '';
}

function booleanChecked(value) {
  return value ? 'checked' : '';
}

function renderSettingsPage() {
  const overview = prefs('overview', { metrics: DEFAULT_METRICS, show_codex_local_tokens: true });
  const refresh = prefs('refresh', { codex_seconds: 60, metrics_seconds: 5, apps_seconds: 30, news_seconds: 0 });
  const news = newsPrefs();
  const appearance = prefs('appearance', { density: 'comfortable', reduce_animations: false });
  const navigation = navigationPrefs();
  const sources = settings?.newsletter_sources || newsletterSources || [];
  const metricOptions = [
    ['cpu', 'CPU'], ['ram', 'RAM'], ['root', 'Disk'], ['uptime', 'Uptime'], ['download', 'Download'], ['upload', 'Upload'], ['codex', 'Codex usage'],
  ];
  const navOptions = [['overview', 'Overview'], ['apps', 'Apps'], ['news', 'News'], ['stocks', 'Stocks'], ['settings', 'Settings']];
  contentEl.innerHTML = `<section class="panel settings-intro"><div class="panel-head"><div><p class="eyebrow">Settings</p><h2>Preferences only</h2><p class="section-copy">Every control here is wired to visible dashboard behavior. Operational status belongs in a separate system area.</p></div></div></section>
    <form id="settings-form" class="settings-form">
      <section class="panel settings-section"><div class="settings-section-head"><div><p class="eyebrow">Overview</p><h2>Cards</h2></div><button class="button secondary" type="button" data-reset-metric-order="true">Reset card order</button></div>
        <div class="settings-grid"><fieldset><legend>Metric cards</legend>${metricOptions.map(([value, label]) => `<label class="check-row"><input type="checkbox" name="overview_metrics" value="${value}" ${checkboxChecked(overview.metrics, value)} /> ${label}</label>`).join('')}</fieldset>
        <label class="check-row"><input type="checkbox" name="overview_show_codex_local_tokens" ${booleanChecked(overview.show_codex_local_tokens)} /> Show local token count under Codex</label></div>
      </section>
      <section class="panel settings-section"><div class="settings-section-head"><div><p class="eyebrow">Refresh intervals</p><h2>Polling preferences</h2></div></div>
        <div class="settings-grid"><label>Codex usage<select name="refresh_codex_seconds"><option value="60" ${optionSelected(refresh.codex_seconds, 60)}>1 min</option><option value="300" ${optionSelected(refresh.codex_seconds, 300)}>5 min</option><option value="900" ${optionSelected(refresh.codex_seconds, 900)}>15 min</option></select></label>
        <label>Metrics fallback polling<select name="refresh_metrics_seconds"><option value="5" ${optionSelected(refresh.metrics_seconds, 5)}>5 sec</option><option value="15" ${optionSelected(refresh.metrics_seconds, 15)}>15 sec</option><option value="30" ${optionSelected(refresh.metrics_seconds, 30)}>30 sec</option></select></label>
        <label>App health<select name="refresh_apps_seconds"><option value="15" ${optionSelected(refresh.apps_seconds, 15)}>15 sec</option><option value="30" ${optionSelected(refresh.apps_seconds, 30)}>30 sec</option><option value="60" ${optionSelected(refresh.apps_seconds, 60)}>1 min</option></select></label>
        <label>News auto-refresh<select name="refresh_news_seconds"><option value="0" ${optionSelected(refresh.news_seconds, 0)}>Manual only</option><option value="900" ${optionSelected(refresh.news_seconds, 900)}>15 min</option><option value="3600" ${optionSelected(refresh.news_seconds, 3600)}>1 hour</option></select></label></div>
      </section>
      <section class="panel settings-section"><div class="settings-section-head"><div><p class="eyebrow">News</p><h2>Reading preferences</h2></div></div>
        <div class="settings-grid"><label>Default source<select name="news_default_source"><option value="" ${optionSelected(news.default_source, '')}>All sources</option>${sources.map((source) => `<option value="${escapeHtml(source.name)}" ${optionSelected(news.default_source, source.name)}>${escapeHtml(source.name)}</option>`).join('')}</select></label>
        <label>Latest News page size<select name="news_page_size"><option value="10" ${optionSelected(news.page_size, 10)}>10</option><option value="20" ${optionSelected(news.page_size, 20)}>20</option><option value="30" ${optionSelected(news.page_size, 30)}>30</option></select></label>
        <fieldset><legend>Curated categories</legend>${['Semiconductor', 'Stocks', 'AI'].map((value) => `<label class="check-row"><input type="checkbox" name="news_curated_categories" value="${value}" ${checkboxChecked(news.curated_categories, value)} /> ${value}</label>`).join('')}</fieldset>
        <label class="check-row"><input type="checkbox" name="news_hide_roundups" ${booleanChecked(news.hide_roundups)} /> Hide roundups/digests in Latest News and curation</label></div>
      </section>
      <section class="panel settings-section"><div class="settings-section-head"><div><p class="eyebrow">Appearance</p><h2>Visual preferences</h2></div></div>
        <div class="settings-grid"><label>Accent color<input name="accent" type="color" value="${escapeHtml(settings?.accent || '#ffffff')}" /></label>
        <label>Card density<select name="appearance_density"><option value="comfortable" ${optionSelected(appearance.density, 'comfortable')}>Comfortable</option><option value="compact" ${optionSelected(appearance.density, 'compact')}>Compact</option></select></label>
        <label class="check-row"><input type="checkbox" name="appearance_reduce_animations" ${booleanChecked(appearance.reduce_animations)} /> Reduce animations</label></div>
      </section>
      <section class="panel settings-section"><div class="settings-section-head"><div><p class="eyebrow">Navigation</p><h2>Tabs</h2></div></div>
        <div class="settings-grid"><label>Default landing tab<select name="navigation_landing_tab">${navOptions.map(([value, label]) => `<option value="${value}" ${optionSelected(navigation.landing_tab, value)}>${label}</option>`).join('')}</select></label>
        <fieldset><legend>Visible tabs</legend>${navOptions.map(([value, label]) => `<label class="check-row"><input type="checkbox" name="navigation_visible_tabs" value="${value}" ${checkboxChecked(navigation.visible_tabs, value)} ${value === 'settings' ? 'disabled' : ''} /> ${label}</label>`).join('')}<input type="hidden" name="navigation_visible_tabs" value="settings" /></fieldset></div>
      </section>
      <div class="settings-sticky-actions"><span id="settings-message" class="modal-message">Preference changes are saved to dashboard.config.json.</span><button class="button primary" type="submit">Save preferences</button></div>
    </form>`;
}

function renderPlaceholder(route) {
  const labels = { news: 'Newsletters', stocks: 'Stocks' };
  contentEl.innerHTML = `<section class="panel placeholder-panel">
    <p class="eyebrow">Coming next</p>
    <h2>${labels[route]}</h2>
    <p class="placeholder-copy">This module has not loaded yet. Try refreshing.</p>
  </section>`;
}

function render() {
  updateNav();
  if (currentRoute !== 'overview') cleanupOverviewWidgets();
  if (currentRoute === 'overview') renderOverview();
  else if (currentRoute === 'apps') renderAppsPage();
  else if (currentRoute === 'news') renderNewsPage();
  else if (currentRoute === 'stocks') renderStocksPage();
  else if (currentRoute === 'settings') renderSettingsPage();
  else renderPlaceholder(currentRoute);
}

function portsHtml(ports = []) {
  if (!ports.length) return '<div class="empty compact">No ports detected.</div>';
  return `<div class="ports-list">${ports.map((port) => `<div class="port-row"><span>:${port.port}</span><strong>${port.open ? 'open' : 'closed'}</strong><small>${port.known_owner ? escapeHtml(port.known_owner.name) : 'no known owner'}${port.conflict ? ' · conflict' : ''}</small></div>`).join('')}</div>`;
}

function backupsHtml(backups = []) {
  if (!backups.length) return '<div class="empty compact">No compose backups yet. Saving or archiving compose will create one.</div>';
  return `<div class="backup-list">${backups.slice(0, 8).map((backup) => `<div class="backup-row"><span>${escapeHtml(backup.backup_id)}</span><small>${Math.round((backup.bytes || 0) / 1024)} KB</small><button class="button secondary" data-restore-backup="${escapeHtml(backup.backup_id)}">Restore</button></div>`).join('')}</div>`;
}

function statsHtml(containers = []) {
  if (!containers.length) return '<div class="empty compact">No running container stats.</div>';
  return `<div class="ports-list">${containers.map((container) => `<div class="port-row"><span>${escapeHtml(container.name || container.container || 'container')}</span><strong>${escapeHtml(container.cpu || '—')}</strong><small>${escapeHtml(container.memory || '')} · ${escapeHtml(container.network || '')}</small></div>`).join('')}</div>`;
}

async function openApp(appId, initialTab = 'compose') {
  selectedApp = await api(`/api/apps/${encodeURIComponent(appId)}`);
  const [compose, logs, stats] = await Promise.all([
    api(`/api/apps/${encodeURIComponent(appId)}/compose`),
    api(`/api/apps/${encodeURIComponent(appId)}/logs?tail=160`).catch((error) => ({ ok: false, output: error.message })),
    api(`/api/apps/${encodeURIComponent(appId)}/stats`).catch(() => ({ containers: [] })),
  ]);
  modalEl.innerHTML = `<div class="modal-backdrop" data-close="true"></div>
    <section class="modal-panel" role="dialog" aria-modal="true" aria-label="${escapeHtml(selectedApp.name)} manager">
      <header class="modal-head">
        <div>
          <p class="eyebrow">App Manager</p>
          <h2>${escapeHtml(selectedApp.name)}</h2>
        </div>
        <button class="icon-button" data-close="true">✕</button>
      </header>
      <div class="detail-grid">
        <div class="detail-card"><span>Container</span><strong>${escapeHtml(selectedApp.status)}</strong></div>
        <div class="detail-card"><span>Web health</span><strong>${escapeHtml(healthLabel(selectedApp))}</strong></div>
        <div class="detail-card"><span>Services</span><strong>${selectedApp.services.map(escapeHtml).join(', ') || '—'}</strong></div>
        <div class="detail-card"><span>Appdata</span><strong title="${escapeHtml(selectedApp.appdata_path || '')}">${escapeHtml(selectedApp.appdata_path || '—')}</strong></div>
        <div class="detail-card wide"><span>Compose</span><strong title="${escapeHtml(compose.path)}">${escapeHtml(compose.path)}</strong></div>
      </div>
      <div class="modal-actions">
        ${openHref(selectedApp) ? `<a class="button" href="${openHref(selectedApp)}" target="_blank" rel="noreferrer">Open web UI</a>` : ''}
        <button class="button" data-compose-action="start">Start</button>
        <button class="button" data-compose-action="stop">Stop</button>
        <button class="button" data-compose-action="restart">Restart</button>
        <button class="button danger" data-archive-app="true">Archive</button>
      </div>
      <div class="tab-row">
        <button class="tab-button" data-tab="compose">Compose</button>
        <button class="tab-button" data-tab="logs">Logs</button>
        <button class="tab-button" data-tab="ops">Ops</button>
      </div>
      <section class="tab-panel" data-tab-panel="compose">
        <label class="compose-label">docker-compose.yml</label>
        <textarea id="compose-editor" spellcheck="false">${escapeHtml(compose.content)}</textarea>
        <div class="modal-actions footer-actions">
          <button class="button primary" data-save-compose="true">Save compose</button>
          <span id="modal-message" class="modal-message">Saving creates a local backup first. It does not restart containers.</span>
        </div>
      </section>
      <section class="tab-panel" data-tab-panel="logs">
        <div class="modal-actions footer-actions">
          <label class="compose-label" for="log-tail">docker compose logs</label>
          <select id="log-tail"><option value="160">160 lines</option><option value="300">300 lines</option><option value="600">600 lines</option><option value="1000">1000 lines</option></select>
          <button class="button secondary" data-refresh-logs="true">Refresh logs</button>
        </div>
        <pre id="log-viewer" class="log-viewer">${escapeHtml(logs.output || 'No logs returned.')}</pre>
      </section>
      <section class="tab-panel" data-tab-panel="ops">
        <div class="ops-grid">
          <article class="ops-card"><h3>Ports</h3>${portsHtml(selectedApp.ports)}</article>
          <article class="ops-card"><h3>Container stats</h3>${statsHtml(stats.containers)}</article>
          <article class="ops-card"><h3>Compose backups</h3><div id="backup-list">${backupsHtml(selectedApp.backups)}</div></article>
          <article class="ops-card"><h3>Health check</h3><p>${escapeHtml(selectedApp.health?.error || healthLabel(selectedApp))}</p><button class="button secondary" data-refresh-health="true">Recheck health</button></article>
        </div>
      </section>
    </section>`;
  modalEl.hidden = false;
  setModalTab(initialTab);
  scrollLogsToBottom();
}

function openAddAppModal() {
  modalEl.innerHTML = `<div class="modal-backdrop" data-close="true"></div>
    <section class="modal-panel" role="dialog" aria-modal="true" aria-label="Add app">
      <header class="modal-head"><div><p class="eyebrow">Manual install</p><h2>Add App</h2></div><button class="icon-button" data-close="true">✕</button></header>
      <form id="add-app-form" class="add-form">
        <div class="form-grid">
          <label>App ID<input name="id" required pattern="[a-z0-9][a-z0-9-]{1,62}[a-z0-9]" placeholder="my-service" /></label>
          <label>Name<input name="name" required placeholder="My Service" /></label>
          <label>Web UI port<input name="web_ui_port" type="number" min="1" max="65535" placeholder="8080" /></label>
          <label>Web UI path<input name="web_ui_path" placeholder="/" value="/" /></label>
        </div>
        <label>Description<textarea name="description" class="small-textarea" placeholder="What this app does"></textarea></label>
        <label>docker-compose.yml<textarea name="compose" id="new-compose" required spellcheck="false" placeholder="services:"></textarea></label>
        <div id="add-app-message" class="modal-message">Paste compose. The dashboard validates YAML and checks known port conflicts before saving.</div>
        <div class="modal-actions footer-actions"><button type="submit" class="button primary">Save app</button><button type="button" class="button secondary" data-close="true">Cancel</button></div>
      </form>
    </section>`;
  modalEl.hidden = false;
}

function setModalTab(tab) {
  document.querySelectorAll('[data-tab]').forEach((button) => button.classList.toggle('active', button.dataset.tab === tab));
  document.querySelectorAll('[data-tab-panel]').forEach((panel) => panel.hidden = panel.dataset.tabPanel !== tab);
}

function scrollLogsToBottom() {
  const viewer = document.querySelector('#log-viewer');
  if (viewer) viewer.scrollTop = viewer.scrollHeight;
}

function closeModal() {
  modalEl.hidden = true;
  modalEl.innerHTML = '';
  selectedApp = null;
}

async function refreshApps() {
  setStatus('Refreshing…');
  apps = await api('/api/apps?health=true');
  setStatus('Live', 'ok');
  render();
}

async function runComposeAction(action, appId = selectedApp?.id) {
  if (!appId) return;
  const message = document.querySelector('#modal-message');
  if (message) message.textContent = `${action} running…`;
  setStatus(`${action}…`);
  try {
    const result = await api(`/api/apps/${encodeURIComponent(appId)}/${action}`, { method: 'POST' });
    if (!result.ok) {
      const detail = result.output || 'docker compose returned a non-zero exit';
      if (message) message.textContent = `${action} failed: ${detail}`;
      setStatus(`${action} failed`, 'error');
      await refreshApps();
      return;
    }
    if (message) message.textContent = `${action} complete${result.app?.status ? ` · now ${result.app.status}` : ''}`;
    apps = await api('/api/apps?health=true');
    if (selectedApp?.id === appId) selectedApp = await api(`/api/apps/${encodeURIComponent(appId)}`);
    setStatus('Live', 'ok');
    render();
  } catch (error) {
    if (message) message.textContent = `${action} error: ${error.message}`;
    setStatus(`Error: ${error.message}`, 'error');
  }
}

async function saveCompose() {
  const message = document.querySelector('#modal-message');
  const content = document.querySelector('#compose-editor').value;
  message.textContent = 'Saving compose…';
  try {
    const result = await api(`/api/apps/${encodeURIComponent(selectedApp.id)}/compose`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    selectedApp = await api(`/api/apps/${encodeURIComponent(selectedApp.id)}`);
    document.querySelector('#backup-list').innerHTML = backupsHtml(selectedApp.backups);
    message.textContent = `Compose saved. Backup: ${result.backup?.backup_id || 'created'}. Restart when ready.`;
  } catch (error) {
    message.textContent = `Save failed: ${error.message}`;
  }
}

async function restoreBackup(backupId) {
  if (!selectedApp || !confirm(`Restore compose backup ${backupId}? This will overwrite the current compose file but will create a pre-restore backup first.`)) return;
  const message = document.querySelector('#modal-message');
  if (message) message.textContent = 'Restoring backup…';
  await api(`/api/apps/${encodeURIComponent(selectedApp.id)}/compose/restore`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ backup_id: backupId }),
  });
  await openApp(selectedApp.id, 'compose');
}

async function archiveSelectedApp() {
  if (!selectedApp) return;
  const phrase = `archive ${selectedApp.id}`;
  if (prompt(`Type "${phrase}" to archive this app from the dashboard. Containers/appdata are not deleted.`) !== phrase) return;
  await api(`/api/apps/${encodeURIComponent(selectedApp.id)}`, { method: 'DELETE' });
  closeModal();
  await refreshApps();
}

async function refreshLogs() {
  const viewer = document.querySelector('#log-viewer');
  const tail = document.querySelector('#log-tail')?.value || '160';
  if (!viewer || !selectedApp) return;
  viewer.textContent = 'Loading logs…';
  try {
    const logs = await api(`/api/apps/${encodeURIComponent(selectedApp.id)}/logs?tail=${encodeURIComponent(tail)}`);
    viewer.textContent = logs.output || 'No logs returned.';
  } catch (error) {
    viewer.textContent = `Failed to load logs: ${error.message}`;
  }
  scrollLogsToBottom();
}

async function refreshHealth() {
  if (!selectedApp) return;
  const result = await api(`/api/apps/${encodeURIComponent(selectedApp.id)}/health`);
  alert(`${selectedApp.name}: ${result.health.status}${result.health.http_code ? ` ${result.health.http_code}` : ''}${result.health.error ? `\n${result.health.error}` : ''}`);
}

async function submitAddApp(form) {
  const message = document.querySelector('#add-app-message');
  const data = Object.fromEntries(new FormData(form).entries());
  const payload = {
    id: data.id,
    name: data.name,
    description: data.description || null,
    category: 'Manual',
    web_ui_port: data.web_ui_port ? Number(data.web_ui_port) : null,
    web_ui_path: data.web_ui_path || '/',
    compose: data.compose,
  };
  message.textContent = 'Saving app…';
  try {
    const result = await api('/api/apps', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    });
    closeModal();
    await refreshApps();
    await openApp(result.app.id, 'ops');
  } catch (error) {
    const ports = error.detail?.ports?.map((port) => `:${port.port} ${port.known_owner?.name || (port.open ? 'in use' : 'open')}`).join(', ');
    message.textContent = ports ? `${error.message}: ${ports}` : `Save failed: ${error.message}`;
  }
}

function openTextModal(title, eyebrow, text) {
  modalEl.innerHTML = `<div class="modal-backdrop" data-close="true"></div>
    <section class="modal-panel compact-modal" role="dialog" aria-modal="true" aria-label="${escapeHtml(title)} details">
      <header class="modal-head"><div><p class="eyebrow">${escapeHtml(eyebrow)}</p><h2>${escapeHtml(title)}</h2></div><button class="icon-button" data-close="true">✕</button></header>
      <pre id="agent-detail-output" class="log-viewer small-log">${escapeHtml(text)}</pre>
    </section>`;
  modalEl.hidden = false;
}

async function showProfileDetail(profileId) {
  openTextModal(profileId, 'Agent details', formatProfileDetail(profileId));
}

async function showSessionDetail(sessionId) {
  openTextModal(sessionId, 'Agent / sub-agent transcript', `Loading session ${sessionId}…`);
  const out = document.querySelector('#agent-detail-output');
  const data = await api(`/api/agents/sessions/${encodeURIComponent(sessionId)}`);
  if (out) out.textContent = data.messages?.length
    ? data.messages.map((msg) => `[${msg.timestamp || ''}] ${msg.role}${msg.tool_name ? ` → ${msg.tool_name}` : ''}\n${msg.content || ''}`).join('\n\n---\n\n')
    : JSON.stringify(data, null, 2);
}

async function showCronOutputs(jobId, profile = 'dashcraft') {
  openTextModal(jobId, 'Cron output', `Loading cron outputs ${jobId}…`);
  const out = document.querySelector('#agent-detail-output');
  const data = await api(`/api/agents/cron/${encodeURIComponent(jobId)}/outputs?profile=${encodeURIComponent(profile)}`);
  if (out) out.textContent = data.outputs?.length
    ? data.outputs.map((item) => `# ${item.name} (${item.modified_at})
${item.preview}`).join('\n\n---\n\n')
    : 'No cron outputs found.';
}

async function runCronAction(jobId, action, profile = 'dashcraft') {
  const verb = action === 'run' ? 'run this cron job now' : action === 'remove' ? 'permanently delete this cron job' : `${action} this cron job`;
  if (!confirm(`Hermes will ${verb}: ${jobId}. Continue?`)) return;
  const result = await api(`/api/agents/cron/${encodeURIComponent(jobId)}/${encodeURIComponent(action)}?profile=${encodeURIComponent(profile)}`, { method: 'POST' });
  if (action !== 'remove') openTextModal(jobId, `Cron ${action}`, result.output || JSON.stringify(result, null, 2));
  await refreshMissionControl();
}

async function runAgentAction(agentId, action) {
  const out = document.querySelector('#agent-output');
  if (out) out.textContent = `${action} ${agentId}…`;
  if (action === 'history') {
    const data = await api(`/api/agents/${encodeURIComponent(agentId)}/history`);
    if (out) out.textContent = data.runs.length ? data.runs.map((run) => `[${run.status}] ${run.started_at}\n${run.summary_line || ''}\n${run.log_tail || ''}`).join('\n\n---\n\n') : 'No history yet.';
    return;
  }
  await api(`/api/agents/${encodeURIComponent(agentId)}/${action}`, { method: 'POST' });
  agents = await api('/api/agents');
  render();
}

async function refreshNews(source = newsSourceFilter, limit = newsVisibleLimit, options = {}) {
  newsSourceFilter = source || '';
  newsVisibleLimit = limit || NEWS_PAGE_SIZE;
  setStatus(options.loadingLabel || (newsSourceFilter ? `Filtering ${newsSourceFilter}…` : 'Loading news…'));
  const params = new URLSearchParams({ limit: String(newsVisibleLimit) });
  if (newsSourceFilter) params.set('source', newsSourceFilter);
  try {
    [newsletters, dailyCuration] = await Promise.all([
      api(`/api/newsletters?${params.toString()}`),
      api('/api/newsletters/curation').catch(() => null),
    ]);
    setStatus(options.doneLabel || 'Live', 'ok');
  } catch (error) {
    setStatus(`News filter error: ${error.message}`, 'error');
    throw error;
  }
  if (currentRoute === 'news') {
    renderNewsPage();
    if (Number.isInteger(options.scrollToIndex)) {
      requestAnimationFrame(() => {
        const target = document.querySelector(`[data-news-index="${options.scrollToIndex}"]`);
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      });
    }
  }
}

async function loadMoreNews() {
  const previousCount = newsletters.length;
  const nextLimit = Math.min(newsVisibleLimit + NEWS_PAGE_SIZE, NEWS_MAX_VISIBLE);
  const label = newsSourceFilter ? `Loading more ${newsSourceFilter}…` : 'Loading more news…';
  await refreshNews(newsSourceFilter, nextLimit, { loadingLabel: label, scrollToIndex: previousCount });
  setStatus(`Showing ${newsletters.length} articles`, 'ok');
}

async function saveSettings(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  const overviewMetrics = selectedFormValues(form, 'overview_metrics');
  const visibleTabs = selectedFormValues(form, 'navigation_visible_tabs');
  const curatedCategories = selectedFormValues(form, 'news_curated_categories');
  const preferences = {
    overview: {
      metrics: overviewMetrics,
      show_codex_local_tokens: preferenceBool(data.overview_show_codex_local_tokens),
    },
    refresh: {
      codex_seconds: preferenceNumber(data.refresh_codex_seconds, 60, 60, 900),
      metrics_seconds: preferenceNumber(data.refresh_metrics_seconds, 5, 5, 30),
      apps_seconds: preferenceNumber(data.refresh_apps_seconds, 30, 15, 60),
      news_seconds: preferenceNumber(data.refresh_news_seconds, 0, 0, 3600),
    },
    news: {
      default_source: data.news_default_source || '',
      page_size: preferenceNumber(data.news_page_size, 10, 10, 30),
      curated_categories: curatedCategories.length ? curatedCategories : ['Semiconductor', 'Stocks', 'AI'],
      hide_roundups: preferenceBool(data.news_hide_roundups),
    },
    appearance: {
      density: data.appearance_density || 'comfortable',
      reduce_animations: preferenceBool(data.appearance_reduce_animations),
    },
    navigation: {
      landing_tab: data.navigation_landing_tab || 'overview',
      visible_tabs: [...new Set([...(visibleTabs.length ? visibleTabs : ['overview', 'apps', 'news', 'stocks']), 'settings'])],
    },
  };
  const payload = { accent: data.accent || '#ffffff', preferences };
  const result = await api('/api/settings', { method: 'PUT', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) });
  settings = result.settings;
  applyPreferences({ forceNewsSource: true });
  configureRefreshIntervals();
  setStatus('Settings saved', 'ok');
  currentRoute = routeFromHash();
  render();
}

function applyPreferences({ forceNewsSource = false } = {}) {
  const news = newsPrefs();
  const sourceNames = new Set((settings?.newsletter_sources || []).map((source) => source.name));
  const preferredSource = sourceNames.has(news.default_source) ? news.default_source : '';
  if (forceNewsSource || !newsSourceFilter) newsSourceFilter = preferredSource;
  newsVisibleLimit = preferenceNumber(news.page_size, NEWS_PAGE_SIZE, 10, NEWS_MAX_VISIBLE);
  const appearance = prefs('appearance', { density: 'comfortable', reduce_animations: false });
  const accent = settings?.accent || '#ffffff';
  document.documentElement.style.setProperty('--accent', accent);
  document.documentElement.dataset.density = appearance.density || 'comfortable';
  document.documentElement.dataset.reduceAnimations = appearance.reduce_animations ? 'true' : 'false';
  const navigation = navigationPrefs();
  if (!navigation.visible_tabs.includes(currentRoute)) currentRoute = routeFromHash();
  updateNav();
}

function setManagedInterval(key, fn, seconds) {
  if (refreshTimers[key]) clearInterval(refreshTimers[key]);
  if (!seconds || seconds <= 0) {
    refreshTimers[key] = null;
    return;
  }
  refreshTimers[key] = setInterval(fn, seconds * 1000);
}

function configureRefreshIntervals() {
  const refresh = prefs('refresh', { codex_seconds: 60, metrics_seconds: 5, apps_seconds: 30, news_seconds: 0 });
  setManagedInterval('mission', () => refreshMissionControl().catch(() => {}), preferenceNumber(refresh.codex_seconds, 60, 60, 900));
  setManagedInterval('metrics', async () => {
    try {
      metrics = await api('/api/metrics');
      uptimeEl.textContent = `Pi uptime ${metrics.uptime_hours}h`;
      if (currentRoute === 'overview') {
        const metricsEl = document.querySelector('#metrics');
        if (metricsEl) metricsEl.innerHTML = metricsHtml(metrics);
        document.querySelectorAll('.system-monitor-widget').forEach((el) => { el.innerHTML = metricsHtml(metrics); });
      }
    } catch (_) {}
  }, preferenceNumber(refresh.metrics_seconds, 5, 5, 30));
  setManagedInterval('apps', async () => {
    try {
      apps = await api('/api/apps?health=true');
      if (currentRoute === 'overview') drawOverviewApps();
      if (currentRoute === 'apps') renderAppsPage();
    } catch (_) {}
  }, preferenceNumber(refresh.apps_seconds, 30, 15, 60));
  setManagedInterval('news', () => {
    if (currentRoute === 'news') refreshNews(newsSourceFilter, newsVisibleLimit, { loadingLabel: 'Refreshing news…', doneLabel: 'News refreshed' }).catch(() => {});
  }, preferenceNumber(refresh.news_seconds, 0, 0, 3600));
}

async function load() {
  setStatus('Loading…');
  try {
    const [nextMetrics, appRows, settingRows] = await Promise.all([
      api('/api/metrics'),
      api('/api/apps?health=true').catch(() => []),
      api('/api/settings').catch(() => ({ stocks: [], pinned_app_ids: [] })),
    ]);
    metrics = nextMetrics;
    apps = appRows;
    agents = [];
    missionControl = null;
    newsletterSources = [];
    settings = settingRows;
    newsletterSources = Array.isArray(settings.newsletter_sources) ? settings.newsletter_sources : [];
    if (!window.location.hash) currentRoute = routeFromHash();
    applyPreferences();
    configureRefreshIntervals();
    overviewPinnedAppIds = Array.isArray(settings.pinned_app_ids) ? settings.pinned_app_ids : [];
    const legacyPinned = readLocalArray('overviewAppIds', []);
    if (!overviewPinnedAppIds.length && legacyPinned.length) {
      await setOverviewAppIds(legacyPinned);
      localStorage.removeItem('overviewAppIds');
    }
    ops = null;
    stockAudit = null;
    newsletters = [];
    dailyCuration = null;
    
    // Codex usage is loaded from the last good browser cache immediately, then refreshed asynchronously by Mission Control.
    
    uptimeEl.textContent = `Pi uptime ${metrics.uptime_hours}h`;
    setStatus('Live', 'ok');
    render();
    refreshMissionControl();
    loadSecondaryData();
  } catch (error) {
    setStatus(`API error: ${error.message}`, 'error');
    contentEl.innerHTML = '<div class="empty">Backend is not reachable.</div>';
  }
}

let dragState = null;

function moveItem(list, fromId, toId) {
  const next = [...list];
  const from = next.indexOf(fromId);
  const to = next.indexOf(toId);
  if (from === -1 || to === -1 || from === to) return next;
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

contentEl.addEventListener('dragstart', (event) => {
  const metric = event.target.closest('[data-metric-id]');
  const overviewApp = event.target.closest('[data-overview-app-card]');
  const availableApp = event.target.closest('[data-available-app-id]');
  const office = event.target.closest('[data-office-id]');
  if (metric) dragState = { type: 'metric', id: metric.dataset.metricId };
  else if (overviewApp) dragState = { type: 'overview-app', id: overviewApp.dataset.overviewAppCard };
  else if (availableApp) dragState = { type: 'available-app', id: availableApp.dataset.availableAppId };
  else if (office) dragState = { type: 'office', id: office.dataset.officeId };
  else return;
  event.dataTransfer.effectAllowed = 'move';
});

contentEl.addEventListener('dragover', (event) => {
  if (!dragState) return;
  if (event.target.closest('[data-metric-id], [data-overview-app-card], [data-office-id], #overview-app-grid')) {
    event.preventDefault();
  }
});

contentEl.addEventListener('drop', async (event) => {
  if (!dragState) return;
  const metricTarget = event.target.closest('[data-metric-id]');
  const appTarget = event.target.closest('[data-overview-app-card]');
  const officeTarget = event.target.closest('[data-office-id]');
  const appGrid = event.target.closest('#overview-app-grid');
  event.preventDefault();
  if (dragState.type === 'metric' && metricTarget) {
    const order = orderedIds('overviewMetricOrder', ['cpu', 'ram', 'root', 'uptime', 'codex'], Object.keys(METRIC_BUILDERS));
    writeLocalArray('overviewMetricOrder', moveItem(order, dragState.id, metricTarget.dataset.metricId));
    const metricsEl = document.querySelector('#metrics');
    if (metricsEl) metricsEl.innerHTML = metricsHtml(metrics);
  }
  if (dragState.type === 'overview-app' && appTarget) {
    await setOverviewAppIds(moveItem(overviewAppIds(), dragState.id, appTarget.dataset.overviewAppCard));
    drawOverviewApps();
  }
  if (dragState.type === 'available-app' && appGrid) {
    await setOverviewAppIds([...overviewAppIds(), dragState.id]);
    drawOverviewApps();
  }
  if (dragState.type === 'office' && officeTarget && dragState.id !== officeTarget.dataset.officeId) {
    const officeOrder = readLocalArray('officeOrder', (missionControl?.profiles || []).map((p) => p.id));
    writeLocalArray('officeOrder', moveItem(officeOrder, dragState.id, officeTarget.dataset.officeId));
  }
  dragState = null;
});

contentEl.addEventListener('input', (event) => {
  if (event.target.closest('#overview-search')) drawOverviewApps();
  const calc = event.target.closest('.calculator-widget');
  if (calc) {
    const expression = calc.querySelector('[name="expression"]')?.value || '';
    calc.querySelector('[data-calc-result]').textContent = evaluateCalculatorExpression(expression) || '—';
  }
});

contentEl.addEventListener('click', async (event) => {
  const manager = document.querySelector('#widget-manager');
  if (manager && !manager.hidden && !event.target.closest('#widget-manager, .overview-floating-nav')) manager.hidden = true;
  const deleteButton = event.target.closest('[data-delete-widget]');
  if (deleteButton && overviewGrid) {
    const widget = deleteButton.closest('.grid-stack-item');
    if (widget) overviewGrid.removeWidget(widget);
    persistOverviewGrid();
    return;
  }
  const bookmarkDelete = event.target.closest('[data-delete-bookmark]');
  if (bookmarkDelete) {
    writeBookmarks(readBookmarks().filter((item) => item.id !== bookmarkDelete.dataset.deleteBookmark));
    const body = bookmarkDelete.closest('.overview-widget-body');
    if (body) body.innerHTML = bookmarksHtml();
    return;
  }
  if (event.target.closest('[data-toggle-bookmark-form]')) {
    const form = event.target.closest('.bookmark-widget')?.querySelector('.bookmark-form');
    if (form) form.hidden = !form.hidden;
    return;
  }
  const webviewAction = event.target.closest('[data-webview-action]');
  if (webviewAction) {
    const widget = webviewAction.closest('[data-webview-widget]');
    const frame = widget?.querySelector('iframe');
    const action = webviewAction.dataset.webviewAction;
    if (frame) {
      if (action === 'refresh') frame.src = frame.src;
      else {
        try { frame.contentWindow.history[action](); } catch (_) {}
      }
    }
    return;
  }
  if (event.target.closest('[data-add-widget]')) {
    const manager = document.querySelector('#widget-manager');
    if (manager) manager.hidden = !manager.hidden;
    return;
  }
  const catalogButton = event.target.closest('.widget-catalog-card[data-widget-type]');
  if (catalogButton && overviewGrid) {
    const type = catalogButton.dataset.widgetType;
    const item = overviewWidgetCatalogItem(type);
    addOverviewWidget({ id: `${type}-${Date.now()}`, type, w: item.w, h: item.h });
    return;
  }
  const overviewAdd = event.target.closest('[data-overview-add]');
  if (overviewAdd) {
    await setOverviewAppIds([...overviewAppIds(), overviewAdd.dataset.overviewAdd]);
    drawOverviewApps();
    return;
  }
  if (event.target.closest('[data-toggle-overview-picker]')) {
    const picker = document.querySelector('.overview-picker');
    if (picker) picker.hidden = !picker.hidden;
    return;
  }
  const overviewRemove = event.target.closest('[data-overview-remove]');
  if (overviewRemove) {
    await setOverviewAppIds(overviewAppIds().filter((id) => id !== overviewRemove.dataset.overviewRemove));
    drawOverviewApps();
    return;
  }
  const refresh = event.target.closest('[data-refresh-apps]');
  const add = event.target.closest('[data-add-app]');
  if (refresh || add) return;
  const agentButton = event.target.closest('[data-agent-id]');
  if (agentButton) {
    runAgentAction(agentButton.dataset.agentId, agentButton.dataset.agentAction).catch((error) => alert(error.message));
    return;
  }
  if (event.target.closest('[data-refresh-agents]')) {
    refreshMissionControl().catch((error) => alert(error.message));
    return;
  }
  const profileButton = event.target.closest('[data-profile-detail]');
  if (profileButton) {
    showProfileDetail(profileButton.dataset.profileDetail).catch((error) => alert(error.message));
    return;
  }
  const sessionButton = event.target.closest('[data-session-detail]');
  if (sessionButton) {
    showSessionDetail(sessionButton.dataset.sessionDetail).catch((error) => alert(error.message));
    return;
  }
  const cronOutput = event.target.closest('[data-cron-output]');
  if (cronOutput) {
    showCronOutputs(cronOutput.dataset.cronOutput, cronOutput.dataset.cronProfile).catch((error) => alert(error.message));
    return;
  }
  const cronAction = event.target.closest('[data-cron-action]');
  if (cronAction) {
    runCronAction(cronAction.dataset.cronId, cronAction.dataset.cronAction, cronAction.dataset.cronProfile).catch((error) => alert(error.message));
    return;
  }
  if (event.target.closest('[data-fetch-news]')) {
    setStatus('Fetching RSS…');
    api('/api/newsletters/fetch', { method: 'POST' }).then(() => refreshNews()).then(() => setStatus('Live', 'ok')).catch((error) => setStatus(`News error: ${error.message}`, 'error'));
    return;
  }
  if (event.target.closest('[data-refresh-news]')) {
    refreshNews(newsSourceFilter, newsVisibleLimit).catch((error) => alert(error.message));
    return;
  }
  if (event.target.closest('[data-load-more-news]')) {
    loadMoreNews().catch((error) => alert(error.message));
    return;
  }
  const newsSource = event.target.closest('[data-news-source]');
  if (newsSource) {
    refreshNews(newsSource.dataset.newsSource, preferenceNumber(prefs('news', {}).page_size, NEWS_PAGE_SIZE, 10, NEWS_MAX_VISIBLE)).catch((error) => alert(error.message));
    return;
  }
  const newsItem = event.target.closest('[data-news-id]');
  if (newsItem) {
    return;
  }
  if (event.target.closest('[data-refresh-stock-audit]')) {
    refreshStockAudit().catch((error) => setStatus(`Stock audit error: ${error.message}`, 'error'));
    return;
  }
  if (event.target.closest('[data-reset-metric-order]')) {
    localStorage.removeItem('overviewMetricOrder');
    setStatus('Overview card order reset', 'ok');
    return;
  }
  const target = event.target.closest('[data-app-id]');
  if (!target) return;
  const action = target.dataset.action;
  const appId = target.dataset.appId;
  if (['start', 'stop', 'restart'].includes(action)) runComposeAction(action, appId);
  if (action === 'logs') openApp(appId, 'logs').catch((error) => alert(error.message));
  if (action === 'details') openApp(appId).catch((error) => alert(error.message));
});

contentEl.addEventListener('change', (event) => {
  const stockPacketSelect = event.target.closest('[data-stock-packet]');
  if (stockPacketSelect) {
    stockSelectedReviewId = stockPacketSelect.value;
    const packet = selectedStockPacket();
    const tickers = Array.isArray(packet.tickers) ? packet.tickers.map((item) => String(item).toUpperCase()) : [];
    stockSelectedTicker = tickers.includes('AAPL') ? 'AAPL' : (tickers[0] || stockSelectedTicker);
    refreshStockAudit().catch((error) => setStatus(`Stock audit error: ${error.message}`, 'error'));
    return;
  }
  const stockTickerSelect = event.target.closest('[data-stock-ticker]');
  if (stockTickerSelect) {
    stockSelectedTicker = stockTickerSelect.value;
    refreshStockAudit().catch((error) => setStatus(`Stock audit error: ${error.message}`, 'error'));
  }
});

modalEl.addEventListener('click', (event) => {
  if (event.target.closest('[data-close]')) closeModal();
  const tab = event.target.closest('[data-tab]')?.dataset.tab;
  if (tab) setModalTab(tab);
  const action = event.target.closest('[data-compose-action]')?.dataset.composeAction;
  if (action) runComposeAction(action);
  if (event.target.closest('[data-save-compose]')) saveCompose();
  if (event.target.closest('[data-refresh-logs]')) refreshLogs();
  if (event.target.closest('[data-refresh-health]')) refreshHealth();
  if (event.target.closest('[data-archive-app]')) archiveSelectedApp();
  const backupId = event.target.closest('[data-restore-backup]')?.dataset.restoreBackup;
  if (backupId) restoreBackup(backupId).catch((error) => alert(error.message));
});

modalEl.addEventListener('submit', (event) => {
  if (event.target.id === 'add-app-form') {
    event.preventDefault();
    submitAddApp(event.target);
  }
});

contentEl.addEventListener('submit', (event) => {
  if (event.target.id === 'overview-search-form' || event.target.classList.contains('widget-search-form')) {
    event.preventDefault();
    const query = event.target.querySelector('input[type="search"]')?.value?.trim();
    if (query) window.open(`https://www.google.com/search?q=${encodeURIComponent(query)}`, '_blank', 'noopener,noreferrer');
    return;
  }
  if (event.target.classList.contains('webview-toolbar')) {
    event.preventDefault();
    const widget = event.target.closest('[data-widget-id]');
    const frame = event.target.closest('[data-webview-widget]')?.querySelector('iframe');
    const url = safeUrl(new FormData(event.target).get('url'));
    if (widget) writeWebviewUrl(widget.dataset.widgetId, url);
    if (frame) frame.src = url;
    return;
  }
  if (event.target.classList.contains('bookmark-form')) {
    event.preventDefault();
    const data = new FormData(event.target);
    const title = String(data.get('title') || '').trim();
    let url = String(data.get('url') || '').trim();
    if (!title || !url) return;
    if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
    writeBookmarks([...readBookmarks(), { id: `bookmark-${Date.now()}`, title, url }]);
    const body = event.target.closest('.overview-widget-body');
    if (body) body.innerHTML = bookmarksHtml();
    return;
  }
  if (event.target.id === 'settings-form') {
    event.preventDefault();
    saveSettings(event.target).catch((error) => alert(error.message));
  }
});

document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modalEl.hidden) closeModal(); });
window.addEventListener('hashchange', () => { currentRoute = routeFromHash(); if (window.location.hash.replace('#', '') !== currentRoute) window.location.hash = currentRoute; render(); });

async function loadSecondaryData() {
  try {
    const [agentRows, sourceRows, opsRows, newsRows, curateRows] = await Promise.all([
      api('/api/agents').catch(() => []),
      api('/api/newsletters/sources').catch(() => []),
      api('/api/metrics/ops').catch(() => null),
      api(`/api/newsletters?limit=${newsVisibleLimit}${newsSourceFilter ? `&source=${encodeURIComponent(newsSourceFilter)}` : ''}`).catch(() => []),
      api('/api/newsletters/curation').catch(() => null),
    ]);
    if (!missionControl) agents = agentRows;
    newsletterSources = sourceRows;
    ops = opsRows;
    newsletters = newsRows;
    dailyCuration = curateRows;
    if (currentRoute !== 'overview') render();
  } catch (_) {}

  api('/api/stocks/review-options')
    .then((stockReviewRows) => {
      stockReviewOptions = stockReviewRows;
      stockAudit = stockReviewRows;
      if (stockReviewRows?.ok) ensureStockSelectionFromOptions(stockReviewRows);
      if (currentRoute === 'stocks') renderStocksPage();
      if (stockReviewRows?.ok) refreshStockAudit({ quiet: true }).catch(() => {});
    })
    .catch((error) => {
      stockReviewOptions = { ok: false, status: 'unavailable', reason: error.message };
      stockAudit = stockReviewOptions;
      if (currentRoute === 'stocks') renderStocksPage();
    });
}

async function refreshStockAudit({ quiet = false } = {}) {
  if (!quiet) setStatus('Refreshing stock audit…');
  ensureStockSelectionFromOptions(stockReviewOptions || stockAudit || {});
  stockAudit = await api(stockAuditUrl()).catch((error) => ({ ok: false, status: 'unavailable', reason: error.message }));
  if (!quiet) setStatus(stockAudit.ok ? 'Stock audit loaded' : 'Stock audit unavailable', stockAudit.ok ? 'ok' : 'error');
  if (currentRoute === 'stocks') renderStocksPage();
}

async function refreshMissionControl() {
  if (missionControlRefreshInFlight) return;
  missionControlRefreshInFlight = true;
  try {
    const data = await api('/api/agents/mission-control').catch(() => null);
    const project = data?.trading_project;
    if (!data) return;
    missionControl = data;
    agents = data.configured_agents || agents;
    if (project) {
      updateAgentTokenDataFromProject(project);
    }
    if (currentRoute === 'overview') {
      const metricsEl = document.querySelector('#metrics');
      if (metricsEl) metricsEl.innerHTML = metricsHtml(metrics);
      drawOverviewApps();
    }
  } catch (_) {
  } finally {
    missionControlRefreshInFlight = false;
  }
}

// Refresh intervals are configured from Settings after initial preferences load.

load();
try {
  const stream = new EventSource('/api/metrics/stream');
  stream.onmessage = (event) => {
    metrics = JSON.parse(event.data);
    uptimeEl.textContent = `Pi uptime ${metrics.uptime_hours}h`;
    if (currentRoute === 'overview') {
      const metricsEl = document.querySelector('#metrics');
      if (metricsEl) metricsEl.innerHTML = metricsHtml(metrics);
    }
  };
} catch (_) {}
