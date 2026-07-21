# Overview Responsive CSS and Dock-Clearance Refactor

Status: approved implementation specification

Repository: `/home/juyoungoh/nas/Projects/dashboard`

Baseline: pushed `main` / `origin/main` commit `83e280a`

Design input: the uncommitted `frontend/style.css` diff produced by task `t_4df781a8`. It is not a released revision and must not be reset, stashed, cleaned, or treated as a commit.

## 1. Goal

Replace the accumulated late Overview overrides with one contiguous, ordered source-of-truth section for widget structure, quiet/edit chrome, responsive reflow, Weather, and floating-dock clearance. The fixed search/Add/pin dock stays. The result must preserve the current product while making a saved 7×3 expanded Weather widget show six complete forecast cards and making every essential Overview widget/control scroll to a dock-safe position.

This is a bounded frontend structural refactor, not an Overview redesign.

## 2. Inspected current behavior

The following facts were re-verified before approving this specification:

- `HEAD` and `origin/main` are `83e280a`; `frontend/style.css` has a 47-line uncommitted diff (26 insertions, 21 deletions).
- The live service answers `GET /api/health` with `{"ok":true}` and serves `/static/style.css?v=20260721-weather-expansion` from the dirty checkout. This proves the current behavior, not a released commit.
- `/api/overview` currently contains both a compact `3×3` Weather widget and a second saved `7×3` Weather widget. The second is representative test data and must not be deleted or rewritten by this refactor.
- At the live 1280-pixel browser width, the 7×3 Weather body is about 586 px wide; the prior 1440×900 evidence measured about 652 px. Six hourly DOM articles exist, but at the narrower width the current 100 px minimum cards overflow the hourly container.
- Calling `scrollIntoView({block: 'end'})` on the live expanded Weather places its body and hourly row under the dock. Measured at the inspected 1280×633 browser viewport: Weather widget `top=403`, `bottom=633`; dock `top=551`, `bottom=615`; the dock intersects the Weather body/hourly row and cards 1–5. The root has no `scroll-padding-bottom` and the widget has no `scroll-margin-bottom`.
- The current dock spacing variable is `--overview-dock-space: 160px`; `.content` receives 184 px bottom padding. The spare document height permits more manual scrolling, but browser-native `scrollIntoView({block:'end'})` does not stop above the dock. Clearance therefore is not an invariant yet.
- The reviewed 390×844 normal evidence has a readable one-column reflow, all five primary destinations, and no persistent management header/drag/delete chrome. The explicit management/pin capture restores those controls.
- The prior expanded-Weather crop is not valid pass evidence: the fixed dock covers most of the hourly row. DOM card count alone does not prove visual completeness.
- The project has no local npm dependency tree. `/usr/bin/chromium` is available; `npm view playwright version` resolves, so browser QA may install `playwright-core` only under `/tmp` without adding a project dependency.

## 3. Approved file scope

Implementation may modify only:

- `frontend/style.css` — consolidate the Overview CSS and implement the geometry/layout contract.
- `frontend/app.js` — replace dock-height spacing with measured obstacle clearance and, if needed, split hourly Weather fields into stable markup. Do not change API semantics.
- `frontend/index.html` — cache-busting query strings only, after CSS/JS changes.
- `docs/overview-responsive-refactor.md` — include this approved specification in the implementation commit; only correct factual drift discovered during implementation.

No other file is in scope. Preserve every unrelated modified, deleted, and untracked path exactly.

## 4. CSS ownership and consolidation

### 4.1 One contiguous source of truth

`frontend/style.css` must have one contiguous `Overview` section, bounded by explicit start/end comments. No active Overview selector may be appended after that end marker. Keep the section in this physical source order; do not introduce CSS cascade layers into this otherwise unlayered stylesheet:

1. Overview tokens and Gridstack/item structure.
2. Floating dock, search, and widget-manager geometry.
3. Widget shell, body, and content-specific components.
4. Quiet versus explicit-management chrome and resize handles.
5. Calendar and Weather component rules.
6. Responsive viewport rules (`820px`, then `640px` if still needed).
7. Weather container query.

The source order is the layer model. State selectors and breakpoint selectors must appear once. Do not append a final “fix,” “maturity pass,” or “iteration” block.

### 4.2 Current-rule disposition

Line numbers below refer to the inspected dirty file and are anchors only; selectors, not line numbers, determine the edit.

| Current area | Decision |
| --- | --- |
| `509–551`, old `Dashy-like Overview frontend prototype` (`.overview-hero`, `.overview-layout`, `.web-card`, `.sticky-widgets`, etc.) | Delete. Repository search found no live JS/HTML use. |
| `554–605`, original movable/resizable base | Retain the active grid/widget/content rules, but rewrite them as the first Overview source-of-truth layer. Delete unused `.overview-board-head` / `.overview-board-actions` rules. |
| `613–643`, first dock and hover-chrome block | Merge dock/search/bookmark declarations into the dock/component layers. Move chrome declarations to the single state layer. |
| `646–672`, widget manager, first handle visibility, webview/bookmarks | Merge unique manager/webview/bookmark rules. Remove duplicated generic handle-state declarations. |
| `675–686`, grid inset/system/calculator | Retain unique active component rules in the component layer. |
| `689–791`, repeated dock/nav/pin styling and glyphs | Collapse repeated `.overview-floating-nav` and `.nav-icon-button` declarations into one dock block. Preserve the minimal transparent pin state and glyphs. |
| `794–847`, input-widget exceptions and metric clipping | Retain webview/calculator body behavior and metric clipping. Delete the obsolete widget-head position exception at `799–808`; head position is owned once by the chrome layer. |
| `850–877`, full-width bottom hover chrome | Delete. It is contradicted by the later approved top movement chrome. |
| `880–921`, top/side resize experiment | Delete. It is contradicted by the approved bottom/side handles. |
| `924–937`, first Calendar/Weather detail block | Merge only unique declarations into the richer Calendar/Weather component layer. Do not retain duplicate `.calendar-*` / `.weather-widget` definitions. |
| `940–997`, top movement chrome and first bottom handles | Merge the approved top header location and side/bottom handle geometry into the one chrome layer. |
| `999–1043`, repeated handle visibility and enlarged handles | Delete as a separate block. Where its final dimensions are preferred, move those values into the single chrome layer. |
| `1044–1069`, richer Calendar and Weather rules | Retain and rewrite once in the component layer. Replace the Weather layout as specified below. |
| `1070–1112`, late nav/mobile/Weather overrides | Merge the final intended mobile behavior into the responsive layer. Remove duplicate `.content` padding and duplicate earlier mobile-nav display models. Keep one Weather container query. |

### 4.3 Selector ownership rules

After consolidation:

- `.overview-widget` and `.overview-widget-body` each have one structural definition plus only narrowly scoped widget-type exceptions.
- `.overview-widget-head` has one base definition and one reveal/suppress state group. Its position is top inset, never redefined per widget type.
- `.ui-resizable-*` geometry is defined once. Approved handles are `s`, `se`, `sw`, `e`, and `w`; north handles remain absent. Bottom/side handles are hidden at rest and available only in the existing explicit management/edit state.
- `.overview-floating-nav` has one geometry definition and one mobile adjustment, not four successive definitions.
- `.weather-widget`, `.weather-hourly`, and `.weather-hourly article` each have one base definition and one container-query activation.
- `.calendar-events*` has one component definition.
- The final `<=820px` navigation model is one two-column grid with Settings spanning both columns. Remove the earlier competing five-column horizontal-scroll and `display:flex` declarations for the same viewport.
- `!important` is allowed only where needed to override Gridstack’s injected handle styles or to enforce the existing explicit management-state visibility. It must not be used to compensate for duplicate project selectors.

### 4.4 Quiet and explicit-management behavior

Preserve the visual state mapping that already passed review; do not rename the persisted `widgets_pinned` field or alter its backend contract in this task.

- Desktop at rest: no persistent management strip, drag button, delete button, or resize handles. In the existing editable state, top movement/delete chrome and bottom/side handles may appear on hover/focus. In the locked/pinned state, they remain suppressed.
- Mobile normal state at 390×844: `.overview-mobile-reflow` shows content only. `.overview-widget-head`, `.widget-drag-handle`, delete control, and resize handles have zero visible instances. Widget content starts at the standard 12 px inset.
- Mobile explicit existing management/pin state: the current state selector restores the management header and controls. Keep that mapping exactly as the accepted evidence; do not introduce a second persisted mode or redesign the pin control in this bounded refactor.
- Responsive state rules must be written once under `@media (max-width: 820px)`. Do not repair state behavior with selectors after the Weather container query.

The implementation handoff must report the exact body classes/attributes used for normal and explicit captures so the visual reviewer does not have to infer state from screenshot filenames.

## 5. Floating-dock geometry invariant

### 5.1 Geometry tokens

Keep the fixed floating dock. Its bottom edge must respect the device safe area:

```css
.overview-floating-nav {
  bottom: max(18px, env(safe-area-inset-bottom, 0px));
}
```

Replace the height-only meaning of `--overview-dock-space` with one unambiguous measured token, preferably `--overview-bottom-clearance`.

On each Overview render, dock/manager toggle, resize, and orientation change, `frontend/app.js` must calculate:

```text
obstacleTop = minimum top of the visible fixed dock and visible widget manager
bottomClearance = ceil(window.innerHeight - obstacleTop + 16px safety gap)
```

Ignore hidden or zero-area obstacles. The 16 px gap is mandatory. The calculation naturally includes dock height, its bottom/safe-area offset, and the manager height when open.

Use the same token for:

- Overview content bottom padding: `bottomClearance + 24px` page-end breathing room.
- The document scroller’s `scroll-padding-block-end`: `bottomClearance`.

Do not also add the full clearance as `scroll-margin-bottom` to every widget; that would double-count native scroll alignment. A small component-specific scroll margin is allowed only if measured verification shows a browser defect and the reason is documented.

Remove the CSS variable when leaving Overview/no dock is present, so other routes keep their current spacing.

### 5.2 Measurable invariant

With the widget manager closed, for every essential Overview widget/control that fits in the dock-safe viewport, after:

```js
target.scrollIntoView({ block: 'end', inline: 'nearest' });
await new Promise(requestAnimationFrame);
await new Promise(requestAnimationFrame);
```

the following must be true:

```text
targetRect.bottom <= dockRect.top - 16
intersectionArea(targetRect, dockRect) == 0
```

For expanded Weather, apply this to:

- the `.grid-stack-item` outer rectangle;
- `.overview-widget`;
- `.overview-widget-body`;
- `.weather-hourly`;
- each of the first six hourly-card rectangles.

All ten intersection results (item, widget, body, row, and six cards) must be zero. The Weather outer bottom boundary must be visible in the normal viewport.

For other widgets, iterate all `.overview-widget` elements at 1440×900, 1280×800, and 390×844. For a control inside a widget taller than the available safe viewport, verify the focusable control itself instead and record that exception. Search/Add/pin remain fixed controls and are tested directly for viewport containment.

### 5.3 Evidence rule

A `fullPage: true` screenshot is not pass evidence for dock overlap because browsers can stamp fixed elements into a composed full-page image. Required overlap evidence is a normal viewport screenshot (`fullPage: false`) taken after native `scrollIntoView`, paired with the recorded DOM rectangles. A clipped whole-widget image is supplementary and must come from that same dock-safe viewport state.

## 6. Expanded Weather layout contract

### 6.1 Activation

- Compact `3×3` Weather is current-only. `.weather-hourly` must compute to `display:none`, have no visible hourly cards, and cause no document overflow.
- Expanded Weather activates only when the Weather container is large enough for the actual practical state. Use one size container query based on the Weather widget itself, with an approved threshold near `min-width: 620px` and `min-height: 200px`. The saved 7×3 state with about 652 px body/client width must activate; compact 3×3 must not.
- If implementation measurements show the body’s actual query box differs from the prior 652 px measurement, adjust the threshold narrowly and report the measured compact/expanded widths. Do not use viewport width as the Weather activation condition.

### 6.2 Six-card row

At the representative 7×3 / approximately 652 px body width:

- Render one row of exactly the first six hourly cards.
- Use `grid-template-columns: repeat(6, minmax(0, 1fr))` (or an equivalent layout proven by rectangles), with 5–6 px gaps and `min-width:0` cards. Do not retain a 100 px card minimum that makes six cards overflow the actual content box.
- The first six cards must all be fully inside the hourly client rectangle: each card’s `left >= hourly.left`, `right <= hourly.right`, `top >= hourly.top`, and `bottom <= hourly.bottom` (allow at most 1 CSS-pixel rounding tolerance).
- `.weather-hourly.scrollWidth <= .weather-hourly.clientWidth + 1` for the six-card state.
- No second row, clipped bottom, or hidden required field. Internal horizontal scrolling is unnecessary while only six items are rendered; if future markup renders more, cards 1–6 must remain visible before scrolling begins.

### 6.3 Per-card information

Each card visibly includes five independently testable fields:

1. time;
2. temperature;
3. condition;
4. precipitation probability;
5. wind speed.

The current combined/truncated detail string is insufficient evidence. `frontend/app.js` may split it into stable elements/classes such as time, temperature, condition, precipitation, and wind. Preserve escaping and existing data semantics; this is presentation markup only.

Typography constraints:

- Time and metadata: no smaller than 10 px.
- Temperature: 16–18 px.
- Condition: one or two visible lines; clamp at two lines rather than clipping an entire field.
- Precipitation and wind may share one metadata row only if both complete values remain visible at 652 px. Otherwise use two compact rows.
- The result must remain legible in a screenshot without browser zoom.

Do not change Open-Meteo requests, units, stale/error semantics, client-IP location behavior, or the six-item data limit.

### 6.4 Representative non-normal states

The structural rules must not assume a complete six-hour payload. Verify these existing presentation states without changing backend fixtures or contracts:

- Loading/detecting location: short text only; no reserved empty hourly row and no overflow.
- Location/provider error: readable error plus Retry action; the action can be scrolled above the dock.
- Stale cached response: current conditions, stale note, and however many real hourly items the payload supplies; no fabricated placeholder hours.
- Partial hourly response (1–5 valid rows): render only real rows, keep them in one row, and do not stretch unreadable blank cards into a six-column requirement.
- Empty hourly response: current conditions remain usable and the hourly container is absent/hidden.
- Compact 3×3: current-only regardless of whether hourly data exists.

Calendar’s disconnected/server-OAuth-setup state, connected event state, and empty-events state must retain their existing readable layouts. The implementation does not need to manufacture a connected account for screenshots; the focused required capture may use the safe server-setup state already present.

## 7. Responsive and regression contract

### 7.1 Mobile

At 390×844:

- One Gridstack column, no horizontal document overflow, and no saved desktop `x/y/w/h` mutation.
- Primary navigation exposes Overview, Apps, News, Stocks, and Settings without horizontal clipping; Settings spans the two columns.
- Normal content-only state has zero visible widget heads, drag handles, delete controls, and resize handles.
- The explicit management/pin capture shows the expected controls and they are not covered by the dock.
- Compact Weather is current-only.
- Clock, CPU, RAM, calculator, root disk, Calendar, apps, and dock remain readable and reachable.

### 7.2 Desktop

At 1440×900 and 1280×800:

- Preserve Gridstack 12-column coordinates/persistence and `float:true` behavior.
- Preserve top hover movement/delete chrome and bottom/side resize affordances in the applicable existing edit state; no persistent large chrome at rest.
- Preserve the sidebar/topbar hierarchy, dark minimal surfaces, thin borders, apps, metrics, bookmarks, generic Webviews, and floating dock.
- No global summary strip, hero, or replacement KPI strip.

### 7.3 Calendar and minor notes

Calendar must remain a local month grid with the concise server-OAuth setup state. Never add credential-paste, JSON-upload, or Save-client UI.

Two prior Minor findings may be fixed only if trivial within the consolidated rules:

- Keep Clock time and meridiem together at 1440×900; avoid an orphaned standalone `PM`/`AM` line.
- Raise `.calendar-days span` from the current very faint muted token to a readable secondary value. Verify at least 4.5:1 contrast against the rendered Calendar background.

These are non-blocking unless the refactor makes them worse. Do not expand into a Clock or Calendar redesign.

### 7.4 Global quality

- `document.documentElement.scrollWidth - clientWidth == 0` at all required viewports.
- Zero browser console errors and zero uncaught page errors.
- No source/configuration/service/layout mutation outside the approved files and normal service restart.
- Preserve backend/API behavior, News, Stocks, Settings, Apps manager, Calendar OAuth, and Weather fetching semantics.

## 8. Verification and evidence contract

### 8.1 Exact repository checks

Run from `/home/juyoungoh/nas/Projects/dashboard`:

```bash
git status --short --branch
git diff --check
node --input-type=module --check < frontend/app.js
.venv/bin/python -m compileall backend tests
.venv/bin/python -m pytest -q tests/test_overview_integrations.py
.venv/bin/python -m pytest -q tests
```

Then restart and probe the actual live service:

```bash
HOME=/home/juyoungoh systemctl --user restart lab-dashboard.service
HOME=/home/juyoungoh systemctl --user is-active lab-dashboard.service
curl -fsS http://127.0.0.1:8765/api/health
curl -fsS http://127.0.0.1:8765/ | grep -E 'style\.css\?v=|app\.js\?v='
```

The expected service state is `active`; health must return an object with `ok:true`; HTML must reference the new cache-busting CSS/JS versions.

### 8.2 Browser harness

Do not add npm dependencies to the repository. If the worker needs a terminal browser harness, use a task-local directory:

```bash
export OVERVIEW_QA_DIR="/tmp/dashboard-task-${HERMES_KANBAN_TASK}"
mkdir -p "$OVERVIEW_QA_DIR/pw"
npm install --prefix "$OVERVIEW_QA_DIR/pw" playwright-core@1.61.1
export NODE_PATH="$OVERVIEW_QA_DIR/pw/node_modules"
```

Use `/usr/bin/chromium` and `http://127.0.0.1:8765/#overview`. Save the executable QA script and its JSON metrics under `$OVERVIEW_QA_DIR` so the handoff is reproducible. Seed only browser `sessionStorage.overviewClientWeatherLocation` for deterministic presentation evidence; do not write the dashboard API, SQLite, localStorage layout, or saved Gridstack config. The existing saved compact and 7×3 Weather instances are sufficient.

### 8.3 Required fresh screenshots

All must be produced after the live restart from the committed implementation:

- `overview-1440x900.png` — normal viewport, full current viewport.
- `overview-1280x800.png` — normal viewport, full current viewport.
- `overview-390x844-normal-upper.png`.
- `overview-390x844-normal-middle.png`.
- `overview-390x844-normal-lower.png`.
- `overview-390x844-management.png` — explicit existing management/pin state.
- `overview-weather-expanded-safe-1440x900.png` — normal viewport showing the whole saved 7×3 Weather above the dock, with the dock also visible and not intersecting it.
- `overview-weather-expanded-clip.png` — whole-widget clip from the same safe viewport state, with all outer boundaries and six complete cards.
- `overview-calendar-focused.png` — whole Calendar widget.

Optional full-page images must be labeled supplementary and cannot replace the normal-viewport captures.

### 8.4 Required DOM JSON

Record at minimum:

- viewport width/height/DPR and scroll position;
- body classes and management-state attributes;
- document/client width and overflow delta;
- console-error and page-error arrays;
- dock, manager (when open), Gridstack item, widget, body, hourly-row, and first-six-card rectangles;
- saved `gs-w`, `gs-h`, `gs-x`, and `gs-y` for compact and expanded Weather;
- computed `--overview-bottom-clearance`, content bottom padding, root scroll padding, Weather container dimensions, hourly display/layout/overflow;
- six-card visible count and per-card field booleans for time, temperature, condition, precipitation, and wind;
- dock intersection areas after native `scrollIntoView`;
- compact Weather hourly display and visible-card count;
- normal and explicit-management counts for widget heads, drag handles, delete controls, and resize handles;
- Calendar weekday computed foreground/background colors and calculated contrast;
- Clock value rectangle/wrapping result at 1440×900.

## 9. Commit and delivery contract

Stage only the approved frontend files and this document; inspect the staged diff before commit:

```bash
git add frontend/style.css frontend/app.js frontend/index.html docs/overview-responsive-refactor.md
git diff --cached --check
git diff --cached -- frontend/style.css frontend/app.js frontend/index.html docs/overview-responsive-refactor.md
git commit -m "fix: consolidate overview responsive layout"
HOME=/home/juyoungoh git push origin main
git rev-parse HEAD
git rev-parse origin/main
```

If an approved file did not need a change, do not force-touch it. The reported `HEAD` and `origin/main` SHAs must match. Never stage the unrelated dirty/untracked files.

The implementation handoff must include:

- pushed commit SHA and push output;
- changed files;
- exact commands and results/test counts;
- live service and served asset verification;
- browser/DOM JSON path;
- every required screenshot path;
- native Kanban artifacts for screenshots and metrics, not only `/tmp` prose links;
- state assumptions, unresolved issues, and residual risks;
- explicit confirmation that unrelated dirty/untracked files were preserved.

## 10. Acceptance criteria

Implementation is ready for independent visual review only when all are true:

1. One contiguous Overview CSS source-of-truth section exists; the duplicate blocks identified above are deleted/merged, and no late contradictory Overview override remains.
2. The fixed dock respects safe area and the measured native-scroll invariant passes at 1440×900, 1280×800, and 390×844.
3. The saved 7×3 Weather at about 652 px shows six simultaneous, complete, unobstructed cards with all five fields and no second row or horizontal overflow.
4. Compact 3×3 Weather is current-only.
5. Mobile normal and explicit-management states match the visibility/count contract; one-column reflow and full navigation remain intact.
6. Calendar server-OAuth/local-grid presentation and the existing no-strip/dark-minimal Overview are preserved.
7. Tests, syntax checks, live restart/health, console/page errors, and overflow checks pass.
8. Fresh required evidence is attached or delivered as native task artifacts.
9. The implementation is committed and pushed; `HEAD == origin/main` for the reported SHA.
10. The independent visual reviewer reports no Critical or Major findings. A visual pass proves presentation only, not Weather data-provider semantics.

## 11. Non-goals

- No backend, API, database, settings, Calendar OAuth, Weather provider, or saved layout/schema changes.
- No Gridstack upgrade or replacement.
- No global stylesheet architecture rewrite outside the Overview/nav rules implicated here.
- No new widgets, top summary strip, hero, theme, typography system, or dashboard redesign.
- No News, Stocks, Apps-manager, Settings, or trading-pipeline work.
- No cleanup/reset/stash of unrelated repository changes.
- No manual mutation of the user’s saved 3×3 or 7×3 Weather layouts to manufacture evidence.

## 12. Assumptions and residual risks

Assumptions:

- Modern Chromium support for `:has()`, container queries, `env(safe-area-inset-bottom)`, and root `scroll-padding-block-end` is acceptable because the current product already relies on Chromium-era Gridstack and `:has()`.
- The current saved 7×3 Weather instance remains available for non-mutating QA.
- Weather content is time-dependent; only presentation is judged.

Residual risks:

- `frontend/style.css` is global and currently contains years of iterative rules. Even a bounded consolidation can affect non-Overview selectors, so the implementation must inspect Apps/News/Stocks/Settings smoke views for gross regressions without redesigning them.
- The existing mobile management/pin state naming is historically overloaded. This task preserves its accepted visual mapping rather than changing persistence semantics; a later product task may normalize the mode model if requested.
- Browser-native root scroll-padding behavior must be verified in the actual deployed Chromium path. If it fails, use one documented fallback—not another late override—and repeat the full evidence set.
- Full-page screenshot stamping can create false dock-overlap impressions; only normal-viewport geometry plus screenshots is authoritative.
