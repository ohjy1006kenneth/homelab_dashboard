const statusEl = document.querySelector('#api-status');
const metricsEl = document.querySelector('#metrics');
const appGridEl = document.querySelector('#app-grid');
const searchEl = document.querySelector('#app-search');
const uptimeEl = document.querySelector('#uptime');

let apps = [];

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
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
  return (name || '?').trim().slice(0, 1).toUpperCase();
}

function renderApps() {
  const query = searchEl.value.trim().toLowerCase();
  const visible = apps.filter((app) => `${app.name} ${app.description || ''} ${app.category || ''}`.toLowerCase().includes(query));
  if (!visible.length) {
    appGridEl.innerHTML = '<div class="empty">No apps match that search.</div>';
    return;
  }
  appGridEl.innerHTML = visible.map((app) => {
    const icon = app.icon_url
      ? `<img src="${app.icon_url}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('div'), {className:'app-fallback', textContent:'${fallbackInitial(app.name)}'}))" />`
      : `<div class="app-fallback">${fallbackInitial(app.name)}</div>`;
    const open = app.web_ui_url ? `<a class="button" href="${app.web_ui_url}" target="_blank" rel="noreferrer">Open</a>` : '<span class="button disabled">No UI</span>';
    return `<article class="app-card">
      <div class="app-icon">${icon}</div>
      <div class="app-main">
        <div class="app-title"><h3>${app.name}</h3><span class="dot ${statusClass(app.status)}"></span></div>
        <p>${app.description || app.category || app.id}</p>
        <div class="app-meta"><span>${app.status}</span>${app.web_ui_port ? `<span>:${app.web_ui_port}</span>` : ''}</div>
      </div>
      ${open}
    </article>`;
  }).join('');
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
load();
setInterval(async () => {
  try { renderMetrics(await api('/api/metrics')); } catch (_) {}
}, 5000);
