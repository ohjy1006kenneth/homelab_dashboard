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

function metricsHtml(data, expanded = false) {
  if (!data) return '<div class="empty">Metrics loading…</div>';
  const cards = [
    metricCard('CPU', `${Math.round(data.cpu_pct ?? 0)}%`, data.cpu_temp_c ? `${data.cpu_temp_c}°C · load ${data.load_avg?.[0] ?? '—'}` : `load ${data.load_avg?.[0] ?? '—'}`),
    metricCard('RAM', `${data.ram_used_gb} GB`, `${data.ram_pct ?? '—'}% of ${data.ram_total_gb} GB`),
    metricCard('Root disk', `${data.disk_used_gb} GB`, `${data.disk_pct ?? '—'}% of ${data.disk_total_gb} GB`),
    metricCard('Uptime', `${data.uptime_hours} h`, 'since boot'),
  ];
  if (expanded) {
    const nas = data.mounts?.find((mount) => mount.path === '/home/juyoungoh/nas');
    cards.push(
      metricCard('Docker', data.docker?.running ?? '—', `${data.docker?.total ?? '—'} containers total`),
      metricCard('NAS disk', nas ? `${nas.used_gb} GB` : '—', nas ? `${nas.pct}% of ${nas.total_gb} GB` : 'mount unavailable'),
      metricCard('Network RX', `${data.network?.recv_gb ?? '—'} GB`, `${data.network?.avg_recv_kbps ?? '—'} KB/s avg`),
      metricCard('Swap', `${data.swap_used_gb ?? '—'} GB`, `${data.swap_pct ?? '—'}% of ${data.swap_total_gb ?? '—'} GB`),
    );
  }
  return cards.join('');
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

function appMatches(app, query, category, status, health) {
  const haystack = `${app.name} ${app.description || ''} ${app.category || ''} ${app.id}`.toLowerCase();
  const healthOk = !health || (health === 'ok' ? app.health?.ok === true : health === 'bad' ? app.health?.ok === false : app.health?.ok === null);
  return (!query || haystack.includes(query))
    && (!category || app.category === category)
    && (!status || app.status === status)
    && healthOk;
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
      <div class="app-title"><h3>${escapeHtml(app.name)}</h3><span class="dot ${statusClass(app.status)}" title="container ${escapeHtml(app.status)}"></span><span class="dot ${healthClass(app.health)}" title="${escapeHtml(healthLabel(app))}"></span></div>
      <p>${escapeHtml(description)}</p>
      <div class="app-meta"><span>${escapeHtml(app.status)}</span><span>${escapeHtml(healthLabel(app))}</span>${app.web_ui_port ? `<span>:${app.web_ui_port}</span>` : ''}${app.category ? `<span>${escapeHtml(app.category)}</span>` : ''}</div>
    </button>
    <div class="app-actions">${open}<button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="details">Manage</button>${managerActions}</div>
  </article>`;
}

function updateNav() {
  navLinks.forEach((link) => link.classList.toggle('active', link.getAttribute('href') === `#${currentRoute}`));
  titleEl.textContent = ({ overview: 'Overview', apps: 'Apps', agents: 'Agents', news: 'Newsletters', stocks: 'Stocks' })[currentRoute];
}

function renderOverview() {
  contentEl.innerHTML = `<div id="metrics" class="metrics-grid">${metricsHtml(metrics, true)}</div>
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
    const visible = apps.filter((app) => appMatches(app, query, '', '', ''));
    gridEl.innerHTML = visible.length ? visible.map((app) => appCard(app)).join('') : '<div class="empty">No apps match that search.</div>';
  }
  searchEl.addEventListener('input', draw);
  draw();
}

function renderAppsPage() {
  const categoryOptions = categories().map((category) => `<option value="${escapeHtml(category)}">${escapeHtml(category)}</option>`).join('');
  const running = apps.filter((app) => app.status === 'running').length;
  const stopped = apps.filter((app) => app.status === 'stopped').length;
  const healthy = apps.filter((app) => app.health?.ok === true).length;
  const unhealthy = apps.filter((app) => app.health?.ok === false).length;
  contentEl.innerHTML = `<section class="manager-hero">
      ${metricCard('Apps', apps.length, `${apps.filter((app) => app.web_ui_port).length} with web UI`)}
      ${metricCard('Running', running, 'containers active')}
      ${metricCard('Web healthy', healthy, `${unhealthy} failing checks`)}
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
          <select id="health-filter"><option value="">All health</option><option value="ok">Web healthy</option><option value="bad">Web failing</option><option value="none">No UI / unchecked</option></select>
          <button class="button secondary" data-refresh-apps="true">Refresh</button>
          <button class="button primary" data-add-app="true">Add App</button>
        </div>
      </div>
      <div class="hint-row">Open launches the web UI. Manage shows compose, backups, ports, health, and logs. Add App saves a manual compose into dashboard-owned apps/.</div>
      <div id="apps-manager-grid" class="app-grid manager-grid"></div>
    </section>`;

  const searchEl = document.querySelector('#apps-search');
  const categoryEl = document.querySelector('#category-filter');
  const statusFilterEl = document.querySelector('#status-filter');
  const healthFilterEl = document.querySelector('#health-filter');
  const gridEl = document.querySelector('#apps-manager-grid');
  function draw() {
    const query = searchEl.value.trim().toLowerCase();
    const visible = apps.filter((app) => appMatches(app, query, categoryEl.value, statusFilterEl.value, healthFilterEl.value));
    gridEl.innerHTML = visible.length ? visible.map((app) => appCard(app, 'manager')).join('') : '<div class="empty">No apps match those filters.</div>';
  }
  [searchEl, categoryEl, statusFilterEl, healthFilterEl].forEach((el) => el.addEventListener('input', draw));
  document.querySelector('[data-refresh-apps]').addEventListener('click', refreshApps);
  document.querySelector('[data-add-app]').addEventListener('click', openAddAppModal);
  draw();
}

function renderPlaceholder(route) {
  const labels = { agents: 'Agents', news: 'Newsletters', stocks: 'Stocks' };
  contentEl.innerHTML = `<section class="panel placeholder-panel">
    <p class="eyebrow">Coming next</p>
    <h2>${labels[route]}</h2>
    <p class="placeholder-copy">Phase 1/2 app operations are being filled out first. This module comes after the CasaOS replacement workflow is solid.</p>
  </section>`;
}

function render() {
  updateNav();
  if (currentRoute === 'overview') renderOverview();
  else if (currentRoute === 'apps') renderAppsPage();
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
          <label>Category<input name="category" placeholder="Media" value="Manual" /></label>
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
    if (message) message.textContent = result.ok ? `${action} complete` : `${action} failed: ${result.output || 'no output'}`;
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
    category: data.category || 'Manual',
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

async function load() {
  try {
    const [nextMetrics, appRows] = await Promise.all([api('/api/metrics'), api('/api/apps?health=true')]);
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
  const add = event.target.closest('[data-add-app]');
  if (refresh || add) return;
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

document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modalEl.hidden) closeModal(); });
window.addEventListener('hashchange', () => { currentRoute = routeFromHash(); render(); });

load();
setInterval(async () => {
  try {
    metrics = await api('/api/metrics');
    uptimeEl.textContent = `Pi uptime ${metrics.uptime_hours}h`;
    if (currentRoute === 'overview') {
      const metricsEl = document.querySelector('#metrics');
      if (metricsEl) metricsEl.innerHTML = metricsHtml(metrics, true);
    }
  } catch (_) {}
}, 5000);
