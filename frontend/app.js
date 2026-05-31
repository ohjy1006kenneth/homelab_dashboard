const statusEl = document.querySelector('#api-status');
const metricsEl = document.querySelector('#metrics');
const appGridEl = document.querySelector('#app-grid');
const searchEl = document.querySelector('#app-search');
const uptimeEl = document.querySelector('#uptime');
const modalEl = document.querySelector('#app-modal');

let apps = [];
let selectedApp = null;

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

function metricCard(label, value, detail = '') {
  return `<article class="metric-card"><span>${label}</span><strong>${value}</strong><small>${detail}</small></article>`;
}

function renderMetrics(data) {
  metricsEl.innerHTML = [
    metricCard('CPU', `${Math.round(data.cpu_pct ?? 0)}%`, data.cpu_temp_c ? `${data.cpu_temp_c}°C` : 'temp n/a'),
    metricCard('RAM', `${data.ram_used_gb} GB`, `of ${data.ram_total_gb} GB`),
    metricCard('Disk', `${data.disk_used_gb} GB`, `of ${data.disk_total_gb} GB`),
    metricCard('Uptime', `${data.uptime_hours} h`, 'since boot'),
  ].join('');
  uptimeEl.textContent = `Pi uptime ${data.uptime_hours}h`;
}

function statusClass(status) {
  if (status === 'running') return 'running';
  if (status === 'stopped' || status === 'missing') return 'stopped';
  return 'unknown';
}

function fallbackInitial(name) {
  return escapeHtml((name || '?').trim().slice(0, 1).toUpperCase());
}

function renderApps() {
  const query = searchEl.value.trim().toLowerCase();
  const visible = apps.filter((app) => `${app.name} ${app.description || ''} ${app.category || ''}`.toLowerCase().includes(query));
  if (!visible.length) {
    appGridEl.innerHTML = '<div class="empty">No apps match that search.</div>';
    return;
  }
  appGridEl.innerHTML = visible.map((app) => {
    const initial = fallbackInitial(app.name);
    const icon = app.icon_url
      ? `<img src="${app.icon_url}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'), {className:'app-fallback', textContent:'${initial}'}))" />`
      : `<div class="app-fallback">${initial}</div>`;
    const open = app.web_ui_url ? `<a class="button" href="${app.web_ui_url}" target="_blank" rel="noreferrer">Open</a>` : '<span class="button disabled">No UI</span>';
    return `<article class="app-card">
      <div class="app-icon">${icon}</div>
      <button class="app-main app-select" data-app-id="${escapeHtml(app.id)}">
        <div class="app-title"><h3>${escapeHtml(app.name)}</h3><span class="dot ${statusClass(app.status)}"></span></div>
        <p>${escapeHtml(app.description || app.category || app.id)}</p>
        <div class="app-meta"><span>${escapeHtml(app.status)}</span>${app.web_ui_port ? `<span>:${app.web_ui_port}</span>` : ''}</div>
      </button>
      <div class="app-actions">${open}<button class="button secondary" data-app-id="${escapeHtml(app.id)}" data-action="details">Manage</button></div>
    </article>`;
  }).join('');
}

async function openApp(appId) {
  selectedApp = await api(`/api/apps/${encodeURIComponent(appId)}`);
  const compose = await api(`/api/apps/${encodeURIComponent(appId)}/compose`);
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
        <div class="detail-card"><span>Appdata</span><strong>${escapeHtml(selectedApp.appdata_path || '—')}</strong></div>
        <div class="detail-card"><span>Compose</span><strong>${escapeHtml(compose.path)}</strong></div>
      </div>
      <div class="modal-actions">
        ${selectedApp.web_ui_url ? `<a class="button" href="${selectedApp.web_ui_url}" target="_blank" rel="noreferrer">Open web UI</a>` : ''}
        <button class="button" data-compose-action="start">Start</button>
        <button class="button" data-compose-action="stop">Stop</button>
        <button class="button" data-compose-action="restart">Restart</button>
      </div>
      <label class="compose-label">docker-compose.yml</label>
      <textarea id="compose-editor" spellcheck="false">${escapeHtml(compose.content)}</textarea>
      <div class="modal-actions footer-actions">
        <button class="button primary" data-save-compose="true">Save compose</button>
        <span id="modal-message" class="modal-message">Saving does not restart containers.</span>
      </div>
    </section>`;
  modalEl.hidden = false;
}

function closeModal() {
  modalEl.hidden = true;
  modalEl.innerHTML = '';
  selectedApp = null;
}

async function runComposeAction(action) {
  const message = document.querySelector('#modal-message');
  message.textContent = `${action} running…`;
  try {
    const result = await api(`/api/apps/${encodeURIComponent(selectedApp.id)}/${action}`, { method: 'POST' });
    message.textContent = result.ok ? `${action} complete` : `${action} failed: ${result.output || 'no output'}`;
    apps = await api('/api/apps');
    renderApps();
  } catch (error) {
    message.textContent = `${action} error: ${error.message}`;
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

async function load() {
  try {
    const [metrics, appRows] = await Promise.all([api('/api/metrics'), api('/api/apps')]);
    renderMetrics(metrics);
    apps = appRows;
    renderApps();
    statusEl.textContent = 'Live';
    statusEl.className = 'api-status ok';
  } catch (error) {
    statusEl.textContent = `API error: ${error.message}`;
    statusEl.className = 'api-status error';
    appGridEl.innerHTML = '<div class="empty">Backend is not reachable.</div>';
  }
}

searchEl.addEventListener('input', renderApps);
appGridEl.addEventListener('click', (event) => {
  const target = event.target.closest('[data-app-id]');
  if (!target) return;
  if (target.dataset.action === 'details' || target.classList.contains('app-select')) {
    openApp(target.dataset.appId).catch((error) => alert(error.message));
  }
});
modalEl.addEventListener('click', (event) => {
  if (event.target.closest('[data-close]')) closeModal();
  const action = event.target.closest('[data-compose-action]')?.dataset.composeAction;
  if (action) runComposeAction(action);
  if (event.target.closest('[data-save-compose]')) saveCompose();
});
document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modalEl.hidden) closeModal(); });

load();
setInterval(async () => {
  try { renderMetrics(await api('/api/metrics')); } catch (_) {}
}, 5000);
