const statusEl = document.querySelector('#api-status');
const contentEl = document.querySelector('.content');
const titleEl = document.querySelector('.topbar h1');
const uptimeEl = document.querySelector('#uptime');
const modalEl = document.querySelector('#app-modal');
const navLinks = [...document.querySelectorAll('nav a')];

let apps = [];
let metrics = null;
let selectedApp = null;
let currentRoute = routeFromHash();

async function api(path, options) {
  const response = await fetch(path, options);
  const contentType = response.headers.get('content-type') || '';
  const body = contentType.includes('application/json') ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof body === 'object' && body.detail ? body.detail : `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
  return body;
}

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[char]));
}

function routeFromHash() {
  const route = (window.location.hash || '#overview').replace('#', '') || 'overview';
  return ['overview', 'apps', 'agents', 'news', 'stocks'].includes(route) ? route : 'overview';
}

function setStatus(text, state = '') {
  statusEl.textContent = text;
  statusEl.className = `api-status ${state}`.trim();
}

function metricCard(label, value, detail = '') {
  return `<article class="metric-card"><span>${label}</span><strong>${value}</strong><small>${detail}</small></article>`;
}

function metricsHtml(data) {
  if (!data) return '<div class="empty">Metrics loading…</div>';
  return [
    metricCard('CPU', `${Math.round(data.cpu_pct ?? 0)}%`, data.cpu_temp_c ? `${data.cpu_temp_c}°C` : 'temp n/a'),
    metricCard('RAM', `${data.ram_used_gb} GB`, `of ${data.ram_total_gb} GB`),
    metricCard('Disk', `${data.disk_used_gb} GB`, `of ${data.disk_total_gb} GB`),
    metricCard('Uptime', `${data.uptime_hours} h`, 'since boot'),
  ].join('');
}

function statusClass(status) {
  if (status === 'running') return 'running';
  if (status === 'stopped' || status === 'missing') return 'stopped';
  return 'unknown';
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

function categories() {
  return [...new Set(apps.map((app) => app.category).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

function appMatches(app, query, category, status) {
  const haystack = `${app.name} ${app.description || ''} ${app.category || ''} ${app.id}`.toLowerCase();
  return (!query || haystack.includes(query))
    && (!category || app.category === category)
    && (!status || app.status === status);
}

function appCard(app, mode = 'overview') {
  const href = openHref(app);
  const open = href ? `<a class="button" href="${href}" target="_blank" rel="noreferrer">Open</a>` : '<span class="button disabled">No UI</span>';
  const description = app.description || app.category || app.id;
  const managerActions = mode === 'manager'
    ? `<button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="start">Start</button>
       <button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="restart">Restart</button>
       <button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="logs">Logs</button>`
    : '';
  return `<article class="app-card ${mode === 'manager' ? 'app-card-manager' : ''}">
    <div class="app-icon">${appIconHtml(app)}</div>
    <button class="app-main app-select" data-app-id="${escapeHtml(app.id)}" data-action="details">
      <div class="app-title"><h3>${escapeHtml(app.name)}</h3><span class="dot ${statusClass(app.status)}"></span></div>
      <p>${escapeHtml(description)}</p>
      <div class="app-meta"><span>${escapeHtml(app.status)}</span>${app.web_ui_port ? `<span>:${app.web_ui_port}</span>` : ''}${app.category ? `<span>${escapeHtml(app.category)}</span>` : ''}</div>
    </button>
    <div class="app-actions">${open}<button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="details">Manage</button>${managerActions}</div>
  </article>`;
}

function updateNav() {
  navLinks.forEach((link) => link.classList.toggle('active', link.getAttribute('href') === `#${currentRoute}`));
  titleEl.textContent = ({ overview: 'Overview', apps: 'Apps', agents: 'Agents', news: 'Newsletters', stocks: 'Stocks' })[currentRoute];
}

function renderOverview() {
  contentEl.innerHTML = `<div id="metrics" class="metrics-grid">${metricsHtml(metrics)}</div>
    <section class="panel">
      <div class="panel-head">
        <div>
          <p class="eyebrow">Applications</p>
          <h2>Quick launch</h2>
        </div>
        <input id="app-search" type="search" placeholder="Search apps…" />
      </div>
      <div id="app-grid" class="app-grid"></div>
    </section>`;
  const searchEl = document.querySelector('#app-search');
  const gridEl = document.querySelector('#app-grid');
  function draw() {
    const query = searchEl.value.trim().toLowerCase();
    const visible = apps.filter((app) => appMatches(app, query, '', ''));
    gridEl.innerHTML = visible.length ? visible.map((app) => appCard(app)).join('') : '<div class="empty">No apps match that search.</div>';
  }
  searchEl.addEventListener('input', draw);
  draw();
}

function renderAppsPage() {
  const categoryOptions = categories().map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join('');
  const running = apps.filter((app) => app.status === 'running').length;
  const stopped = apps.filter((app) => app.status === 'stopped').length;
  const withUi = apps.filter((app) => app.web_ui_port).length;
  contentEl.innerHTML = `<section class="manager-hero">
      ${metricCard('Apps', apps.length, `${withUi} with web UI`)}
      ${metricCard('Running', running, 'containers active')}
      ${metricCard('Stopped', stopped, 'available to start')}
      ${metricCard('Categories', categories().length, 'from migrated metadata')}
    </section>
    <section class="panel">
      <div class="panel-head manager-head">
        <div>
          <p class="eyebrow">App Manager</p>
          <h2>Manage compose apps</h2>
        </div>
        <div class="manager-filters">
          <input id="apps-search" type="search" placeholder="Search apps…" />
          <select id="category-filter"><option value="">All categories</option>${categoryOptions}</select>
          <select id="status-filter"><option value="">All statuses</option><option value="running">Running</option><option value="stopped">Stopped</option><option value="unknown">Unknown</option><option value="missing">Missing</option></select>
          <button class="button secondary" data-refresh-apps="true">Refresh</button>
        </div>
      </div>
      <div class="hint-row">Open launches the web UI. Manage shows compose + logs. Start/Restart runs docker compose for that app.</div>
      <div id="apps-manager-grid" class="app-grid manager-grid"></div>
    </section>`;

  const searchEl = document.querySelector('#apps-search');
  const categoryEl = document.querySelector('#category-filter');
  const statusFilterEl = document.querySelector('#status-filter');
  const gridEl = document.querySelector('#apps-manager-grid');
  function draw() {
    const query = searchEl.value.trim().toLowerCase();
    const visible = apps.filter((app) => appMatches(app, query, categoryEl.value, statusFilterEl.value));
    gridEl.innerHTML = visible.length ? visible.map((app) => appCard(app, 'manager')).join('') : '<div class="empty">No apps match those filters.</div>';
  }
  [searchEl, categoryEl, statusFilterEl].forEach((el) => el.addEventListener('input', draw));
  document.querySelector('[data-refresh-apps]').addEventListener('click', refreshApps);
  draw();
}

function renderPlaceholder(route) {
  const labels = { agents: 'Agents', news: 'Newsletters', stocks: 'Stocks' };
  contentEl.innerHTML = `<section class="panel placeholder-panel">
    <p class="eyebrow">Coming next</p>
    <h2>${labels[route]}</h2>
    <p class="placeholder-copy">This section is still on the roadmap. The full Apps manager and logs are live now.</p>
  </section>`;
}

function render() {
  updateNav();
  if (currentRoute === 'overview') renderOverview();
  else if (currentRoute === 'apps') renderAppsPage();
  else renderPlaceholder(currentRoute);
}

async function openApp(appId, initialTab = 'compose') {
  selectedApp = await api(`/api/apps/${encodeURIComponent(appId)}`);
  const [compose, logs] = await Promise.all([
    api(`/api/apps/${encodeURIComponent(appId)}/compose`),
    api(`/api/apps/${encodeURIComponent(appId)}/logs?tail=160`).catch((error) => ({ ok: false, output: error.message })),
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
        <div class="detail-card"><span>Status</span><strong>${escapeHtml(selectedApp.status)}</strong></div>
        <div class="detail-card"><span>Services</span><strong>${selectedApp.services.map(escapeHtml).join(', ') || '—'}</strong></div>
        <div class="detail-card"><span>Appdata</span><strong title="${escapeHtml(selectedApp.appdata_path || '')}">${escapeHtml(selectedApp.appdata_path || '—')}</strong></div>
        <div class="detail-card"><span>Compose</span><strong title="${escapeHtml(compose.path)}">${escapeHtml(compose.path)}</strong></div>
      </div>
      <div class="modal-actions">
        ${openHref(selectedApp) ? `<a class="button" href="${openHref(selectedApp)}" target="_blank" rel="noreferrer">Open web UI</a>` : ''}
        <button class="button" data-compose-action="start">Start</button>
        <button class="button" data-compose-action="stop">Stop</button>
        <button class="button" data-compose-action="restart">Restart</button>
      </div>
      <div class="tab-row">
        <button class="tab-button" data-tab="compose">Compose</button>
        <button class="tab-button" data-tab="logs">Logs</button>
      </div>
      <section class="tab-panel" data-tab-panel="compose">
        <label class="compose-label">docker-compose.yml</label>
        <textarea id="compose-editor" spellcheck="false">${escapeHtml(compose.content)}</textarea>
        <div class="modal-actions footer-actions">
          <button class="button primary" data-save-compose="true">Save compose</button>
          <span id="modal-message" class="modal-message">Saving does not restart containers.</span>
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
    </section>`;
  modalEl.hidden = false;
  setModalTab(initialTab);
  scrollLogsToBottom();
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
  apps = await api('/api/apps');
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
    if (message) message.textContent = result.ok ? `${action} complete` : `${action} failed: ${result.output || 'no output'}`;
    apps = await api('/api/apps');
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
    await api(`/api/apps/${encodeURIComponent(selectedApp.id)}/compose`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    message.textContent = 'Compose saved. Restart when ready.';
  } catch (error) {
    message.textContent = `Save failed: ${error.message}`;
  }
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

async function load() {
  try {
    const [nextMetrics, appRows] = await Promise.all([api('/api/metrics'), api('/api/apps')]);
    metrics = nextMetrics;
    apps = appRows;
    uptimeEl.textContent = `Pi uptime ${metrics.uptime_hours}h`;
    setStatus('Live', 'ok');
    render();
  } catch (error) {
    setStatus(`API error: ${error.message}`, 'error');
    contentEl.innerHTML = '<div class="empty">Backend is not reachable.</div>';
  }
}

contentEl.addEventListener('click', (event) => {
  const refresh = event.target.closest('[data-refresh-apps]');
  if (refresh) return;
  const target = event.target.closest('[data-app-id]');
  if (!target) return;
  const action = target.dataset.action;
  const appId = target.dataset.appId;
  if (['start', 'restart'].includes(action)) runComposeAction(action, appId);
  if (action === 'logs') openApp(appId, 'logs').catch((error) => alert(error.message));
  if (action === 'details') openApp(appId).catch((error) => alert(error.message));
});

modalEl.addEventListener('click', (event) => {
  if (event.target.closest('[data-close]')) closeModal();
  const tab = event.target.closest('[data-tab]')?.dataset.tab;
  if (tab) setModalTab(tab);
  const action = event.target.closest('[data-compose-action]')?.dataset.composeAction;
  if (action) runComposeAction(action);
  if (event.target.closest('[data-save-compose]')) saveCompose();
  if (event.target.closest('[data-refresh-logs]')) refreshLogs();
});

document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modalEl.hidden) closeModal(); });
window.addEventListener('hashchange', () => { currentRoute = routeFromHash(); render(); });

load();
setInterval(async () => {
  try {
    metrics = await api('/api/metrics');
    uptimeEl.textContent = `Pi uptime ${metrics.uptime_hours}h`;
    if (currentRoute === 'overview') {
      const metricsEl = document.querySelector('#metrics');
      if (metricsEl) metricsEl.innerHTML = metricsHtml(metrics);
    }
  } catch (_) {}
}, 5000);
