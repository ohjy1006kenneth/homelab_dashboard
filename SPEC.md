# Homelab Dashboard — Full Build Spec (v3)
> Brief for Hermes Agent. Read this entire document before writing any code.
> v3 change: No CasaOS API dependency. Migration script copies all app data out of CasaOS
> and stores it locally. Dashboard manages containers directly via Docker SDK + docker compose CLI.

---

## 1. Project Identity

**Name:** `lab.local`
**Aesthetic:** Clean minimal modern — Linear/Vercel feel. Refined, not sterile.
**CasaOS relationship:** Source of initial data only. After migration, CasaOS can be uninstalled.
**Philosophy:** Features are not final. Every section is a self-contained module.

---

## 2. Migration Strategy (run once, then forget CasaOS)

```
CasaOS filesystem                     Dashboard
─────────────────                     ─────────
/var/lib/casaos/apps/
  jellyfin/
    docker-compose.yml   ──────────►  /home/juyoungoh/nas/Projects/dashboard/apps/jellyfin/
    icon.png             ──────────►    docker-compose.yml  (owned by dashboard)
                                        icon.png
                                        meta.json           (parsed from x-casaos fields)

  nextcloud/             ──────────►  /home/juyoungoh/nas/Projects/dashboard/apps/nextcloud/
    docker-compose.yml                  docker-compose.yml
    ...                                 icon.png
                                        meta.json

                                      /data/dashboard.db
                                        App table row per app
```

The migration script (`scripts/migrate_from_casaos.py`) runs **once**. After it completes:
- Dashboard reads from `/home/juyoungoh/nas/Projects/dashboard/apps/` and SQLite only
- Docker containers are managed via Docker SDK + `docker compose` CLI
- CasaOS is no longer needed for anything

---

## 3. Project Structure

```
/home/juyoungoh/nas/Projects/dashboard/
├── backend/
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   └── routers/
│       ├── metrics.py           ← CPU/RAM/disk/temp (psutil)
│       ├── apps.py              ← app management via Docker SDK + compose files
│       ├── agents.py            ← Hermes agents: run/stop/logs/history
│       └── newsletters.py       ← RSS fetch + AI summaries
│
├── frontend/
│   ├── index.html
│   ├── app.js                   ← page router, edit mode, Gridstack init
│   ├── style.css                ← design tokens + Gridstack overrides
│   ├── widget-registry.js       ← imports + exports all widget definitions
│   └── widgets/                 ← one file per widget type
│       ├── metrics-row.js
│       ├── metric-card.js
│       ├── app-grid.js
│       ├── docker-stats.js
│       ├── agent-table.js
│       ├── agent-log.js
│       ├── newsletter-feed.js
│       ├── stock-chart.js
│       ├── stock-overview.js
│       ├── quick-links.js
│       └── clock.js
│
├── apps/                        ← owned by dashboard after migration
│   ├── jellyfin/
│   │   ├── docker-compose.yml
│   │   ├── icon.png
│   │   └── meta.json
│   ├── nextcloud/
│   │   └── ...
│   └── {app_name}/
│       ├── docker-compose.yml   ← editable, dashboard is source of truth
│       ├── icon.png             ← copied from CasaOS or fetched from Docker Hub
│       └── meta.json            ← parsed metadata (see schema below)
│
├── scripts/
│   └── migrate_from_casaos.py   ← run once to import all CasaOS apps
│
├── cron/
│   └── newsletter_fetcher.py
│
├── data/
│   └── dashboard.db
│
├── dashboard.config.json
├── SPEC.md
├── agent_log.md
└── requirements.txt
```

---

## 4. meta.json Schema (per app)

Parsed from `x-casaos` fields in docker-compose.yml and stored alongside it:

```json
{
  "id": "jellyfin",
  "name": "Jellyfin",
  "description": "The Free Software Media System",
  "category": "Media",
  "author": "Jellyfin",
  "icon": "icon.png",
  "web_ui_port": 8096,
  "web_ui_path": "/",
  "added_at": "2025-01-01T00:00:00Z",
  "source": "casaos"
}
```

This file is the app's identity record. Dashboard reads it instead of querying CasaOS.

---

## 5. Migration Script (`scripts/migrate_from_casaos.py`)

This is the most important script. Run it once before shutting down CasaOS.

```
Algorithm:

1. Scan /var/lib/casaos/apps/ for subdirectories (each is one app)

2. For each app directory:
   a. Read docker-compose.yml
   b. Parse x-casaos metadata block:
      - main service name
      - app title / description / author / category
      - port descriptions (to find web UI port)
      - icon field (may be a URL or local path)
   c. Copy docker-compose.yml → /home/juyoungoh/nas/Projects/dashboard/apps/{app_id}/docker-compose.yml
   d. Resolve icon:
      - If /var/lib/casaos/apps/{app_id}/icon.png exists → copy it
      - Else if x-casaos has icon URL → download it
      - Else → leave blank (frontend shows text fallback)
   e. Write meta.json with parsed fields
   f. Insert or upsert App row in SQLite

3. Also try: casaos-cli app-management show local {app_id} --yaml
   - Use this output if it differs (it includes resolved env vars)
   - Saves as docker-compose.yml, overwriting the raw copy

4. Print migration report:
   "Migrated 12 apps: jellyfin, nextcloud, ... "
   "Skipped 0 apps"
   "Icons found: 10/12"

5. Write migration_report.json to /home/juyoungoh/nas/Projects/dashboard/data/
```

**Run it as:**
```bash
sudo python3 /home/juyoungoh/nas/Projects/dashboard/scripts/migrate_from_casaos.py
```

Requires sudo because `/var/lib/casaos/apps/` is root-owned.

---

## 6. Database Models

```python
class App(SQLModel, table=True):
    id: str                    # app directory name e.g. "jellyfin"
    name: str                  # display name e.g. "Jellyfin"
    description: str | None
    category: str | None
    icon_path: str | None      # relative path to icon.png inside apps/{id}/
    web_ui_port: int | None    # port number for the web UI
    web_ui_path: str = "/"
    compose_path: str          # absolute path to docker-compose.yml
    enabled: bool = True
    added_at: datetime
    source: str = "casaos"     # "casaos" | "manual" | "import"

class AgentRun(SQLModel, table=True):
    id: str
    agent_id: str
    started_at: datetime
    ended_at: datetime | None
    status: str                # running | success | error
    log: str | None
    summary_line: str | None

class NewsletterItem(SQLModel, table=True):
    id: str
    source: str
    title: str
    url: str
    published_at: datetime
    summary: str
    read: bool = False
    fetched_at: datetime
```

---

## 7. App Management Backend (`routers/apps.py`)

No CasaOS dependency. Uses Docker SDK + `docker compose` CLI + local compose files.

```python
import docker
client = docker.from_env()  # connects via /var/run/docker.sock

# How to get container status for an app:
# 1. Read app's docker-compose.yml to find service names
# 2. Look up each container by name via docker SDK
# 3. Report: running | stopped | error | missing
```

### API endpoints

```
GET  /api/apps
     → reads App rows from SQLite
     → enriches each with live container status from Docker SDK
     → returns: [{ id, name, description, icon_url, status, web_ui_url, category }]

GET  /api/apps/{id}
     → full detail: meta + compose content + container stats (CPU%, mem)

POST /api/apps/{id}/start
     → runs: docker compose -f {compose_path} up -d
     → returns: { ok, output }

POST /api/apps/{id}/stop
     → runs: docker compose -f {compose_path} down
     → returns: { ok, output }

POST /api/apps/{id}/restart
     → runs: docker compose -f {compose_path} restart

GET  /api/apps/{id}/logs
     → SSE stream of docker compose logs --follow --tail=100

GET  /api/apps/{id}/compose
     → returns raw compose file content (for editing)

PUT  /api/apps/{id}/compose
     → accepts new compose content, writes to file
     → does NOT auto-restart (user confirms separately)

POST /api/apps
     → add a new app manually (paste compose content + meta fields)
     → writes files, inserts DB row

DELETE /api/apps/{id}
     → stops containers, removes from dashboard (does NOT delete Docker data)
     → marks enabled=false in DB, moves compose to apps/{id}/archived/
```

### Icon serving
```
GET /api/apps/{id}/icon
    → serves the icon.png from apps/{id}/icon.png
    → returns 404 with Content-Type if missing (frontend shows initial fallback)
```

---

## 8. Full Backend API

### Metrics
```
GET  /api/metrics
     → { cpu_pct, ram_used_gb, ram_total_gb, disk_used_gb, disk_total_gb, cpu_temp_c, uptime_hours }
GET  /api/metrics/stream          SSE every 3s
```

### Apps (§7 above)

### Agents
```
GET  /api/agents                  → list agents from config + live status
POST /api/agents/{id}/run         → spawn subprocess, stream stdout
POST /api/agents/{id}/stop        → kill subprocess
GET  /api/agents/{id}/logs/stream → SSE live log
GET  /api/agents/{id}/history     → past AgentRun rows
```

### Newsletters
```
GET  /api/newsletters             → all summaries newest first
GET  /api/newsletters?source=X    → filter by source
POST /api/newsletters/{id}/read   → mark read
POST /api/newsletters/fetch       → trigger manual RSS fetch + summarize
```

---

## 9. Config File

```json
{
  "title": "lab.local",
  "theme": "dark",
  "accent": "#ffffff",
  "agents": [
    {
      "id": "newsletter-agent",
      "name": "Newsletter Fetcher",
      "script": "/home/juyoungoh/nas/Projects/dashboard/cron/newsletter_fetcher.py",
      "schedule": "0 8 * * *",
      "description": "Fetches and summarizes RSS newsletters"
    }
  ],
  "newsletter_sources": [
    { "name": "Semiconductor Engineering", "rss": "https://semiengineering.com/feed/",   "color": "#3b82f6" },
    { "name": "Chips and Cheese",          "rss": "https://chipsandcheese.com/feed/",    "color": "#22c55e" },
    { "name": "Heisenberg",                "rss": "https://heisenberg.kr/feed/",          "color": "#a855f7" }
  ]
}
```

---

## 10. Frontend Layout

```
┌────────────────────────────────────────────────────────┐
│  ◆ lab.local                             [⊙] [settings] │  48px topbar
├────────────┬───────────────────────────────────────────┤
│            │                                           │
│  Overview  │                                           │
│  Apps      │          MAIN CONTENT AREA                │
│  Agents    │                                           │
│  News      │     Section rendered here                 │
│  Stocks    │                                           │
│            │                                           │
│  ────────  │                                           │
│  Pi uptime │                                           │
│            │                                           │
└────────────┴───────────────────────────────────────────┘
  200px fixed               flex: 1
```

### Section: Overview
- Row 1: 4 stat cards — CPU % | RAM | Disk | Temp
- Row 2: App grid — same visual style as CasaOS
  - Icon (from /api/apps/{id}/icon) | Name | Status dot
  - Click → opens web UI in new tab
  - Hover → shows Start / Stop button overlay

### Section: Apps (full app manager)
- Larger card grid with search/filter by category
- Each card: icon + name + status + port + description
- Actions: Open | Start | Stop | Restart | View Logs | Edit Compose
- "Add App" button → paste compose → fills meta form → saves

### Section: Agents
- Table: name | status | last run | duration | [▶] [■]
- Row expand → log viewer (SSE stream, monospace, auto-scroll)
- History tab per agent

### Section: Newsletters
- Source filter tabs: All | Semiconductor Engineering | Chips and Cheese | Heisenberg
- Card list newest first: source badge + title (links to article) + date + summary
- Mark read (fades card)
- [Fetch Now] button

### Section: Stocks
- Two TradingView iframe widgets (dark theme)
- User edits ticker symbols directly in stocks.js

---

## 11. Design Tokens

```css
:root {
  --bg-base:       #080808;
  --bg-surface:    #101010;
  --bg-elevated:   #181818;
  --bg-hover:      #1e1e1e;
  --border:        #252525;
  --border-soft:   #1a1a1a;
  --text-primary:  #ededed;
  --text-secondary:#888;
  --text-muted:    #484848;
  --accent:        #ffffff;
  --accent-dim:    rgba(255,255,255,0.06);
  --green:         #22c55e;
  --red:           #ef4444;
  --yellow:        #f59e0b;
  --blue:          #60a5fa;
  --font-body:     'Geist', sans-serif;
  --font-mono:     'Geist Mono', monospace;
  --sidebar-w:     200px;
  --topbar-h:      48px;
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s6:24px; --s8:32px;
  --r-sm:4px; --r-md:8px; --r-lg:12px;
}
```

**Component rules:**
- Cards: `bg: var(--bg-surface)` + `border: 1px solid var(--border)` + `border-radius: var(--r-md)`
- No box-shadows — borders only
- Status dots: 8px circle — green/yellow/red
- Metric numbers: always `font-family: var(--font-mono)`
- Section header labels: `11px / uppercase / 0.08em letter-spacing / var(--text-muted)`
- Hover rows: `bg: var(--bg-hover)`, `transition: 100ms`
- App icon: 40×40px, `border-radius: var(--r-sm)`, letter fallback if no icon

---

## 12. Widget Grid System (Drag, Drop, Resize, Customize)

The entire dashboard is built on **Gridstack.js** (CDN, no build step).
Every page is a named Gridstack grid. The user can drag, resize, add, and remove
widgets freely. Layout is saved to SQLite per page.

---

### Load Gridstack from CDN (index.html)

```html
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/gridstack@latest/dist/gridstack.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/gridstack@latest/dist/gridstack.all.js"></script>
```

---

### Layout persistence

Layout is stored per page in SQLite and loaded on each visit.

```python
class PageLayout(SQLModel, table=True):
    page_id: str          # "overview", "agents", "newsletters", "stocks", or custom
    layout_json: str      # JSON string — Gridstack serialized widget array
    updated_at: datetime
```

```
GET  /api/layout/{page_id}   → { page_id, widgets: [...] }
PUT  /api/layout/{page_id}   → save layout JSON, return { ok }
```

Gridstack serializes layout automatically:
```javascript
const layout = grid.save()          // → array of widget positions + ids
await saveLayout(pageId, layout)    // PUT /api/layout/{page_id}
```

---

### Widget definition contract

Every widget file in `frontend/widgets/` exports this interface:

```javascript
export default {
  // Identity
  type: 'metrics-row',          // unique string key, used in layout JSON
  label: 'System Metrics',      // shown in widget catalog
  icon: '◈',
  description: 'CPU, RAM, disk, and temperature at a glance',

  // Default size when dragged in from catalog
  defaultW: 12,   // columns (out of 12)
  defaultH: 2,    // rows

  // Minimum size
  minW: 4,
  minH: 1,

  // Widget config schema (optional — shown in widget settings panel)
  configSchema: {
    // example: { source: { type: 'select', options: ['All', 'SemiEng', ...] } }
  },

  // Lifecycle
  async init(el, config) {
    // el = the .grid-stack-item-content div
    // config = per-instance config from layout JSON
    // Set up SSE connections, intervals, event listeners here
  },

  async render(el, config) {
    // Build and insert HTML into el
    // Called on first mount and when config changes
  },

  destroy(el) {
    // Close SSE connections, clear intervals
    // Called when widget is removed or page navigated away
  }
}
```

**Adding a new widget = one new file in `frontend/widgets/` + one import in `widget-registry.js`.
Nothing else changes.**

---

### Widget registry (`frontend/widget-registry.js`)

```javascript
import MetricsRow     from './widgets/metrics-row.js'
import MetricCard     from './widgets/metric-card.js'
import AppGrid        from './widgets/app-grid.js'
import DockerStats    from './widgets/docker-stats.js'
import AgentTable     from './widgets/agent-table.js'
import AgentLog       from './widgets/agent-log.js'
import NewsletterFeed from './widgets/newsletter-feed.js'
import StockChart     from './widgets/stock-chart.js'
import StockOverview  from './widgets/stock-overview.js'
import QuickLinks     from './widgets/quick-links.js'
import Clock          from './widgets/clock.js'

export const WIDGET_REGISTRY = {
  'metrics-row':     MetricsRow,
  'metric-card':     MetricCard,
  'app-grid':        AppGrid,
  'docker-stats':    DockerStats,
  'agent-table':     AgentTable,
  'agent-log':       AgentLog,
  'newsletter-feed': NewsletterFeed,
  'stock-chart':     StockChart,
  'stock-overview':  StockOverview,
  'quick-links':     QuickLinks,
  'clock':           Clock,
}
```

---

### Full widget catalog

| Type | Label | Default size | Description |
|---|---|---|---|
| `metrics-row` | System Metrics | 12×2 | CPU / RAM / Disk / Temp as 4 cards in a row |
| `metric-card` | Single Metric | 3×2 | One metric — config: which one |
| `app-grid` | App Launcher | 8×4 | CasaOS-style icon grid of all apps |
| `docker-stats` | Docker Stats | 6×3 | Container table: name / status / CPU% / mem |
| `agent-table` | Agents | 6×3 | Agent run/stop table with status badges |
| `agent-log` | Agent Log | 6×4 | Log viewer for one agent — config: agent id |
| `newsletter-feed` | Newsletter Feed | 4×4 | Latest summaries — config: source filter |
| `stock-chart` | Stock Chart | 6×4 | TradingView chart — config: ticker symbol |
| `stock-overview` | Market Overview | 6×4 | TradingView market overview widget |
| `quick-links` | Quick Links | 4×2 | Service launcher tiles from config |
| `clock` | Clock | 2×1 | Current time and date |

---

### Edit mode

Controlled by a toggle in the topbar. Stored in `localStorage` (no DB needed).

```
View mode (default):
  - Grid is static, no drag handles
  - Widgets render normally
  - [Edit] button in topbar

Edit mode:
  - Grid becomes interactive (Gridstack active)
  - Each widget shows:
      ┌──────────────────────────────────────┐
      │ ⠿ System Metrics            [⚙] [✕]  │  ← drag handle + settings + remove
      │                                      │
      │   CPU  42%   RAM  1.2/4GB  ...       │
      └──────────────────────────────────────┘
  - Drag handle (⠿) in widget header
  - [⚙] opens widget config panel (if widget has configSchema)
  - [✕] removes widget from grid (with confirm)
  - [+ Add Widget] button appears → opens Widget Catalog drawer
  - Auto-save layout on every drag/resize stop
  - [Done] button exits edit mode
```

---

### Widget Catalog drawer

Slides in from the right when [+ Add Widget] is clicked in edit mode.

```
┌─────────────────────────────────────┐
│  Add Widget                     [✕] │
├─────────────────────────────────────┤
│  System                             │
│  ┌──────────────┐ ┌──────────────┐  │
│  │ ◈            │ │ ◈            │  │
│  │ System       │ │ Single       │  │
│  │ Metrics      │ │ Metric       │  │
│  └──────────────┘ └──────────────┘  │
│  Apps                               │
│  ┌──────────────┐ ┌──────────────┐  │
│  │ ◈            │ │ ◈            │  │
│  │ App          │ │ Docker       │  │
│  │ Launcher     │ │ Stats        │  │
│  └──────────────┘ └──────────────┘  │
│  ...                                │
└─────────────────────────────────────┘
```

Clicking a widget card in the catalog → adds it to the grid at the first available position.

---

### Pages (sidebar navigation)

The sidebar no longer navigates between fixed views. Instead each nav item is a
**named page** — its own Gridstack grid with its own saved layout.

Default pages (can be renamed or deleted by user):
- **Overview** — default layout: metrics-row + app-grid + newsletter-feed
- **Agents** — default: agent-table (full width) + agent-log
- **Newsletters** — default: newsletter-feed (full width, all sources)
- **Stocks** — default: stock-chart + stock-overview

User can:
- Add a new custom page (just a name + empty grid)
- Rename any page
- Delete a non-last page
- Reorder pages via drag in sidebar

Pages stored in SQLite:
```python
class Page(SQLModel, table=True):
    id: str             # slug e.g. "overview", "my-custom-page"
    label: str          # display name
    icon: str           # single unicode char
    order: int          # sidebar order
    created_at: datetime
```

```
GET  /api/pages          → list pages in order
POST /api/pages          → create new page
PUT  /api/pages/{id}     → rename / reorder
DELETE /api/pages/{id}   → delete (also deletes its layout)
```

---

### Default layout JSON (seeded on first run)

```javascript
// Overview page default layout
const OVERVIEW_DEFAULT = [
  { id: 'w1', type: 'metrics-row',     x: 0, y: 0, w: 12, h: 2 },
  { id: 'w2', type: 'app-grid',        x: 0, y: 2, w: 8,  h: 5 },
  { id: 'w3', type: 'newsletter-feed', x: 8, y: 2, w: 4,  h: 5 },
]

// Agents page default layout
const AGENTS_DEFAULT = [
  { id: 'w1', type: 'agent-table', x: 0, y: 0, w: 12, h: 4 },
  { id: 'w2', type: 'agent-log',   x: 0, y: 4, w: 12, h: 4 },
]
```

If `GET /api/layout/{page_id}` returns 404 (new page), seed with the default for that
page_id, or an empty grid for custom pages.

---

### CSS additions for widget chrome (add to style.css)

```css
/* Gridstack overrides — match dashboard theme */
.grid-stack { background: var(--bg-base); }
.grid-stack-item-content {
  background: var(--bg-surface);
  border: 1px solid var(--border);
  border-radius: var(--r-md);
  overflow: hidden;
}

/* Widget header (shown in edit mode) */
.widget-header {
  display: flex;
  align-items: center;
  padding: var(--s2) var(--s3);
  border-bottom: 1px solid var(--border-soft);
  gap: var(--s2);
  height: 36px;
}
.widget-drag-handle {
  cursor: grab;
  color: var(--text-muted);
  font-size: 14px;
}
.widget-title {
  flex: 1;
  font-size: 12px;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.widget-actions { display: flex; gap: var(--s1); }
.widget-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  padding: var(--s1);
  border-radius: var(--r-sm);
}
.widget-btn:hover { background: var(--bg-hover); color: var(--text-primary); }

/* Hide widget chrome in view mode */
body:not(.edit-mode) .widget-header { display: none; }
body.edit-mode .grid-stack-item-content { border-color: var(--border); }
```

---

## 13. Compose Editor (App Configuration UI)

This is the most complex UI piece. Match CasaOS's approach: parse the compose file into
structured form fields so the user never has to hand-edit YAML for common changes.
Provide a raw YAML fallback for advanced edits.

### Two modes, always in sync

```
┌──────────────────────────────────────────────────────────────────┐
│  🎬 Jellyfin                              [Cancel]  [Save & Apply]│
├──────────────────────────────────────────────────────────────────┤
│  [Environment]  [Ports]  [Volumes]  [Image]  [Raw YAML]          │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│   (tab content here)                                             │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

Switching between Form tabs and Raw YAML **always re-parses in both directions**:
- Form → YAML: serialize form state back to compose YAML on Raw tab open
- YAML → Form: parse YAML back into form fields on Form tab open
- If YAML has a parse error, show it inline and disable Save

---

### Tab: Environment Variables

```
┌─────────────────────┬──────────────────────────────┬────┐
│ PUID                │ 1000                         │ ✕  │
│ PGID                │ 1000                         │ ✕  │
│ TZ                  │ Asia/Seoul                   │ ✕  │
│ JELLYFIN_PublishedS…│ http://192.168.1.x:8096      │ ✕  │
└─────────────────────┴──────────────────────────────┴────┘
[+ Add Variable]
```

- Editable key-value table. Both columns are inline text inputs.
- [✕] removes the row.
- [+ Add Variable] appends a blank row.
- Reads from: `services.{main_service}.environment` in compose YAML.

---

### Tab: Ports

```
┌──────────────┬──────────────┬──────────┬────┐
│ Host Port    │ Container    │ Protocol │    │
├──────────────┼──────────────┼──────────┼────┤
│ 8096         │ 8096         │ tcp      │ ✕  │
│ 8920         │ 8920         │ tcp      │ ✕  │
└──────────────┴──────────────┴──────────┴────┘
[+ Add Port]
```

- Reads from: `services.{main_service}.ports`
- Parses both short form (`"8096:8096"`) and long form (`{ target: 8096, published: 8096 }`)
- Changing host port here changes which port the app is accessible on
- Web UI port in meta.json auto-updates to match if it was the old host port

---

### Tab: Volumes

```
┌────────────────────────┬───────────────┬──────────┬────┐
│ Host Path              │ Container Path│ Mode     │    │
├────────────────────────┼───────────────┼──────────┼────┤
│ /DATA/AppData/jellyfin │ /config       │ rw       │ ✕  │
│ /DATA/Media            │ /media        │ ro       │ ✕  │
└────────────────────────┴───────────────┴──────────┴────┘
[+ Add Volume]
```

- Reads from: `services.{main_service}.volumes`
- Mode dropdown: `rw` | `ro`
- Host path has a file-path helper (shows current Pi filesystem, nothing fancy)

---

### Tab: Image

```
Image
────────────────────────────────
jellyfin/jellyfin  [ latest   ▾]
                   [ latest      ]
                   [ 10.9        ]
                   [ 10.8        ]
                   [ nightly     ]

Current digest: sha256:abc123...
[Check for updates]   Last checked: 2 hours ago
```

- Shows image name and editable tag field
- [Check for updates] calls Docker Hub API to compare current digest with latest
- Changing tag here changes `image:` field in compose

---

### Tab: Raw YAML

- Full compose YAML in a **CodeMirror 6** editor (loaded from CDN, no build step)
- YAML syntax highlighting
- Line numbers
- Changes here sync back to form tabs when user switches away
- Parse errors shown as red underline + error banner at top
- This is the escape hatch for anything the form tabs don't cover

```html
<!-- Load CodeMirror from CDN in index.html -->
<script type="module">
  import { EditorView, basicSetup } from 'https://esm.sh/codemirror@6'
  import { yaml } from 'https://esm.sh/@codemirror/lang-yaml'
  // ... wire up to Raw YAML tab container
</script>
```

---

### Save & Apply flow

```
User clicks [Save & Apply]
        ↓
1. Validate: parse YAML — if error, show it, stop
2. Show diff modal:
   ┌─────────────────────────────────────┐
   │ Changes to jellyfin                 │
   │                                     │
   │  environment:                       │
   │ -  TZ: America/Chicago              │
   │ +  TZ: Asia/Seoul                   │
   │                                     │
   │  ports:                             │
   │ -  - "8096:8096"                    │
   │ +  - "9096:8096"                    │
   │                                     │
   │  [Cancel]  [Apply & Restart]        │
   └─────────────────────────────────────┘
3. User confirms → backend:
   a. Write new docker-compose.yml (backup old as docker-compose.yml.bak)
   b. Run: docker compose -f {compose_path} up -d --force-recreate
   c. Stream output back to modal log viewer
   d. Poll container status until running
   e. Show: "✓ jellyfin restarted successfully"
4. On error → show error output, offer [Restore Backup]
```

---

### Backend endpoints for compose editor (add to routers/apps.py)

```
GET  /api/apps/{id}/compose
     → { content: "raw yaml string", parsed: { services: {...} } }

PUT  /api/apps/{id}/compose
     body: { content: "raw yaml string" }
     → validates YAML, writes file, returns { ok, backup_path }

POST /api/apps/{id}/apply
     → docker compose up -d --force-recreate
     → SSE stream of output lines
     → final event: { status: "success"|"error", message }

POST /api/apps/{id}/restore
     → restores docker-compose.yml.bak if it exists
     → returns { ok, restored_from }

GET  /api/apps/{id}/image/updates
     → checks Docker Hub for newer digest
     → returns { current_tag, latest_digest, up_to_date: bool }
```

---

### Compose parser utility (`backend/compose_parser.py`)

Write a small utility module that all compose editor endpoints use:

```python
def parse_compose(yaml_str: str) -> dict:
    """Parse YAML string into compose dict. Raises ValueError on invalid YAML."""

def extract_env(compose: dict, service: str) -> list[dict]:
    """Returns [{ key, value }] from services.{service}.environment"""

def extract_ports(compose: dict, service: str) -> list[dict]:
    """Returns [{ host, container, protocol }] normalizing short/long forms"""

def extract_volumes(compose: dict, service: str) -> list[dict]:
    """Returns [{ host, container, mode }]"""

def extract_image(compose: dict, service: str) -> dict:
    """Returns { name, tag } split from image field"""

def apply_env(compose: dict, service: str, env: list[dict]) -> dict:
    """Writes env list back into compose dict, returns modified dict"""

def apply_ports(compose: dict, service: str, ports: list[dict]) -> dict:
def apply_volumes(compose: dict, service: str, volumes: list[dict]) -> dict:
def apply_image(compose: dict, service: str, image: str, tag: str) -> dict:

def to_yaml(compose: dict) -> str:
    """Serialize compose dict back to clean YAML string"""
```

This keeps all the parsing logic in one testable place. Routers just call these functions.

---

## 13. Hermes System Prompt

```
You are a web developer maintaining a homelab dashboard at /home/juyoungoh/nas/Projects/dashboard/.
The spec is at /home/juyoungoh/nas/Projects/dashboard/SPEC.md — read it before any task.

Rules:
1. Read existing files before modifying them.
2. After backend changes: sudo systemctl restart dashboard
3. After any deploy: http_check("http://localhost:8081") — fix if it fails.
4. Append changes to agent_log.md after each task.
5. Frontend is vanilla JS ES modules — no npm, no build step, no frameworks.
6. Every new widget follows the widget contract in SPEC.md §12. Never add widget logic to app.js.
7. CSS tokens in style.css :root — never hardcode colors or sizes.
8. All numbers use font-family: var(--font-mono).
9. App data comes from /home/juyoungoh/nas/Projects/dashboard/apps/ and SQLite — never from CasaOS API.
10. Backend first, test it, then build frontend.
11. Layout is always saved via PUT /api/layout/{page_id} on every Gridstack change event.
12. Features are not final — build for extension, not completion.
```

---

## 14. Build Order

**Phase 0 — Migration (do this first, while CasaOS is still running)**
> "Build scripts/migrate_from_casaos.py per SPEC.md §5. Run it with sudo. Show me the migration report. Do not proceed until at least one app appears in /home/juyoungoh/nas/Projects/dashboard/apps/."

**Phase 1 — Foundation**
> "Set up /home/juyoungoh/nas/Projects/dashboard/ per the directory structure in SPEC.md. Install deps from requirements.txt. Create the systemd service on port 8081. Verify http_check passes."

**Phase 2 — Backend**
> "Build all routers: metrics (with SSE stream), apps (Docker SDK + local compose files), agents, newsletters, layout (GET/PUT per page), pages (CRUD). Add Page and PageLayout models to models.py. Seed default pages and layouts on first run. Return mock data where real sources aren't ready. Confirm GET /api/apps, GET /api/metrics, and GET /api/layout/overview all return valid JSON."

**Phase 3 — Frontend shell + Gridstack**
> "Build index.html (load Gridstack.js and CodeMirror from CDN), style.css with all design tokens and Gridstack theme overrides from SPEC.md §11 and §12, and app.js that: initializes a Gridstack grid per page, loads layout from /api/layout/{page_id} on page switch, auto-saves on drag/resize stop, handles edit mode toggle. No widget content yet — use placeholder divs. Verify drag and resize work and layout saves to the backend."

**Phase 4 — Widgets (one at a time, start simple)**
> "Build widget-registry.js and the metrics-row widget. SSE from /api/metrics/stream, renders 4 stat cards. Verify it loads in the grid, drags, resizes, and layout persists across refresh."

> "Build app-grid widget: icon grid from /api/apps. Icons from /api/apps/{id}/icon. Click opens web UI. Hover shows start/stop."

> "Build agent-table and agent-log widgets."

> "Build newsletter-feed widget (with source config), clock, and quick-links."

> "Build stock-chart and stock-overview widgets (TradingView iframes)."

> "Build the Widget Catalog drawer and edit mode chrome (drag handle, remove button, settings button) per SPEC.md §12."

**Phase 5 — Apps page + Compose editor**
> "Build docker-stats widget. Build the Apps page default layout with search/filter. Then build the compose editor per SPEC.md §13 — backend compose_parser.py first, round-trip test it, then the tabbed frontend UI, then the diff modal and apply flow."

**Phase 6 — Cron**
> "Build cron/newsletter_fetcher.py. Test manually. Set up crontab."

---

## 15. Requirements.txt

```
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
sqlmodel>=0.0.16
psutil>=5.9.0
requests>=2.31.0
feedparser>=6.0.11
docker>=7.0.0
python-multipart>=0.0.9
httpx>=0.27.0
pyyaml>=6.0.1
```
