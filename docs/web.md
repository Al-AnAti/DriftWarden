# `src/web` — the control plane

`src/web` is the FastAPI application an operator opens in a browser. It has three jobs:

1. **Host the engine.** The reconciler from [`warden.md`](warden.md) is started inside the
   application lifespan, so one `uvicorn` process is the whole product.
2. **Persist durable state.** The SQLite tables (audit log, drift events, adoption buffer,
   whitelist, setting overrides) and the repository the engine writes through live here.
3. **Serve the UI and the JSON API.** Five server-rendered Jinja2 pages, a Server-Sent Events
   stream that keeps them live, and the `/api/...` endpoints the buttons call.

There is **no authentication**. Keep port 8000 on an internal network or behind a proxy.

```
src/web/
├── app.py                 create_app(), the lifespan that wires the engine, the 409 handler
├── runtime.py             WardenRuntime dataclass + get_runtime(request)
├── db/
│   ├── models_db.py       SQLAlchemy 2.0 declarative tables
│   ├── session.py         Database: engine, WAL pragmas, serialised writes, migrations
│   └── repository.py      Store: typed reads and writes; the engine's StateStore
├── routes/
│   ├── views.py           HTML pages: /, /diff/{ref}, /events, /buffer, /config
│   └── api_actions.py     JSON API under /api
├── templates/             base.html + one template per page
└── static/
    ├── css/style.css      badges, tones, buttons, diff gutter, toasts, modal
    └── js/dashboard.js    SSE client, live rendering, modals, chimes, every button handler
```

---

## `app.py`

`create_app()` builds the `FastAPI` instance (title "DriftWarden Control Plane"), mounts
`/static`, includes the two routers and registers one exception handler: any
`DriftWardenError` becomes

```json
HTTP 409  {"ok": false, "error": "AdoptionError", "message": "..."}
```

The module-level `app = create_app()` is what `uvicorn web.app:app --app-dir src` imports.

The **lifespan** is the composition root. On startup, in order:

1. `get_settings()` and `configure_logging` (Loguru to stderr at `LOG_LEVEL`, with
   `enqueue=True` so worker threads can log safely).
2. `Database(settings.database_url).init()` — tables created, migrations applied.
3. `Store(database, hostname)`, `StateProjector`, `DockerWatcher`, and a `GitHubClient`
   unless `ALLOW_MOCK_BASELINE` is set (then `None`).
4. `Reconciler(settings, watcher, store, projector, github)` and
   `app.state.warden = WardenRuntime(...)`.
5. `await reconciler.start()` — the first baseline fetch and observe-only pass happen
   **before** the server accepts requests, so the first page load already has a snapshot.

On shutdown: `reconciler.stop()` then `database.close()`.

Tests swap the watcher by monkeypatching `web.app.DockerWatcher` before entering the
`TestClient` context (see `tests/test_web_api.py`).

## `runtime.py`

`WardenRuntime` is a frozen-shape dataclass holding `settings`, `database`, `store`,
`projector` and `reconciler`. Routes call `get_runtime(request)` and get typed attributes
instead of poking at `request.app.state` with strings. If the lifespan did not run it raises
`RuntimeError`, which is a server misconfiguration rather than a user error.

---

## `db/` — SQLite persistence

### `session.py` — `Database`

* `create_engine(url, connect_args={"check_same_thread": False})` for SQLite so FastAPI's
  worker threads and the engine thread can share the pool.
* Every new SQLite connection runs `PRAGMA journal_mode=WAL`, `synchronous=NORMAL`,
  `busy_timeout=5000`, `foreign_keys=ON`. WAL lets page renders read while the engine writes.
* `write()` is a context manager that takes a process-wide `RLock`, commits on success and
  rolls back on error. **Every write in the project goes through it**, so there is exactly one
  writer at a time and SQLite never reports "database is locked".
* `session()` is the read-only context manager.
* `init()` runs `Base.metadata.create_all` and then `_migrate()`, which adds columns that
  were introduced after a table already existed on an operator's disk. Today that is
  `pending_adoptions.kind` (default `adopt`) and `pending_adoptions.pr_state` (default `open`).
  Add future additive columns to the same dictionary.

### `models_db.py` — tables

| Table | Model | Columns | Written by |
| --- | --- | --- | --- |
| `audit_log` | `AuditLogRow` | `id`, `action` (an `AuditAction` value), `target`, `details`, `hostname`, `timestamp` | `Store.record_audit` (engine and routes) |
| `pending_adoptions` | `PendingAdoptionRow` | `id`, `kind`, `pr_state`, `container_id`, `container_name`, `service_name`, `pr_number`, `pr_url`, `branch`, `created_at`, `expires_at`, `cleared_at`, `outcome` | adoption paths; an entry is "active" while `cleared_at IS NULL` |
| `whitelisted_entities` | `WhitelistedEntityRow` | `id`, `target_type`, `target_identifier`, `reason`, `created_at`; unique on (`target_type`, `target_identifier`) | whitelist routes |
| `drift_events` | `DriftEventRow` | `id`, `ref`, `drift_status`, `previous_status`, `service_name`, `container_name`, `details`, `hostname`, `detected_at` | `_persist_transitions` after every cycle |
| `setting_overrides` | `SettingOverrideRow` | `key` (primary), `value`, `updated_at` | Cluster Config saves |

Timestamps are `DateTime(timezone=True)` and always UTC. SQLite drops the timezone on read,
so the repository re-attaches UTC with `_aware()` before building Pydantic models (which
require `AwareDatetime`).

Nothing about the live host is stored: the snapshot lives in memory on the projector and is
rebuilt on the next cycle after a restart.

### `repository.py` — `Store`

The repository facade. Rows never leave this module as ORM objects; callers get the Pydantic
contracts from `shared`. Every write wraps failures in `DatabaseWriteError`.

| Area | Methods |
| --- | --- |
| Audit | `record_audit(action, target, details) -> id` (also logs `AUDIT ...` at INFO), `list_audit(limit)` newest first |
| Buffer | `add_pending(...)` (supersedes any active entry for the same `container_id` with outcome `superseded`), `active_pending()`, `pending_history(limit)` → `(PendingAdoption, outcome)` pairs, `set_pending_state(id, pr_state)`, `clear_pending(id, outcome)` |
| Whitelist | `list_whitelist()`, `add_whitelist(type, identifier, reason)` (idempotent), `remove_whitelist(id)` → the removed entry or `None` |
| Events | `record_drift_event(...)`, `list_drift_events(limit)` → plain dicts for the template |
| Settings | `list_settings()` → `{key: value}`, `set_setting(key, value)` upsert, `delete_setting(key)` |
| Maintenance | `clear_logs()` → `(events removed, audit rows removed)`, both tables truncated in one transaction (used by `scripts/reset_logs.py`); `counts()` → row counts for the Cluster Config tiles |

`Store` satisfies the `warden.engine.store.StateStore` Protocol, which is how the engine uses
it without importing `web`.

---

## `routes/views.py` — HTML pages

All page handlers are synchronous (FastAPI runs them on the thread pool) and read the
in-memory snapshot; only Events, Buffer and Config touch SQLite.

| Route | Template | Context beyond the base |
| --- | --- | --- |
| `GET /` | `dashboard.html` | `records` sorted problems-first (`STATUS_ORDER`: rogue, drifted, missing, pending, matched; alphabetical within a group) |
| `GET /diff/{ref}` | `diff.html` | `record` (404 if `HostSnapshot.find` returns nothing), `changed_fields`, `expected_lines`, `observed_lines` |
| `GET /events` | `events.html` | `events` (last 200 drift transitions), `audit` (last 200 audit rows) |
| `GET /buffer` | `buffer.html` | `pending` (active entries from the reconciler), `history` (last 100 rows with outcomes) |
| `GET /config` | `config.html` | `editable_settings` (`SettingView`s), `whitelist`, `db_counts`, `baseline`, `excluded_prefixes`, `github_configured` |

`base_context` gives every template the `snapshot`, `snapshot_json` (the same payload the SSE
stream sends, embedded as `window.WARDEN.snapshot`), `counts`, `overall`, the two display
maps below, `settings`, `hostname`, `mode`, `enforcement_active` and the snapshot `version`.

* `STATUS_META` maps each `DriftStatus` value to a label and a Tailwind colour family
  (`matched` emerald, `drifted` amber, `missing` sky, `rogue` rose, `pending_adoption` violet).
* `OVERALL_META` does the same for the headline pill: `IN SYNC`, `SERVICE MISSING`,
  `DRIFT DETECTED`, `DOCKER UNAVAILABLE`, `BASELINE_UNAVAILABLE`.

`line_diff(expected, observed)` powers the two code panes. It runs `difflib.SequenceMatcher`
over the YAML lines and returns a `DiffLine(number, sign, text)` per line per pane: `-` for
lines only in the baseline, `+` for lines only in the live state, empty for lines on both
sides. Because it compares whole lines, a changed `warden.repo` label is flagged without
flagging its `labels:` parent, and the line numbers are per pane.

---

## `routes/api_actions.py` — JSON API

Prefix `/api`. Action endpoints are `async` and await the reconciler directly; the engine's
`DriftWardenError`s surface as 409 through the app handler, unknown refs as 404, and request
body validation failures as 422.

| Method and path | Body | Returns | Purpose |
| --- | --- | --- | --- |
| `GET /health` | — | status, mode, enforcement, docker and baseline flags, `overall`, version | liveness |
| `GET /state` | — | `{"version", "snapshot"}` | the full snapshot (same payload as SSE) |
| `GET /state/stream` | — | `text/event-stream` | live updates, see below |
| `POST /reconcile` | — | `{"version", "snapshot"}` | force a baseline refresh and a `manual` cycle (the 🔄 Refresh button) |
| `GET /containers/{ref}` | — | one `DriftRecord` | record with its structured diff |
| `POST /actions/revert/{ref}` | — | `ActionResult` | recreate a drifted or missing service (Revert / Spin up) |
| `POST /actions/kill/{ref}` | — | `ActionResult` | stop and remove a rogue container |
| `POST /actions/adopt/{ref}` | optional `AdoptionRequest` (`note`) | `ActionResult` with `pr_url` | open the adoption PR |
| `POST /actions/adopt-deletion/{ref}` | optional `AdoptionRequest` | `ActionResult` with `pr_url` | open a PR removing a missing service |
| `POST /actions/batch` | `BatchRequest` (`operation`, `scope`, `note`) | `BatchResult` + `succeeded`, `failed` | Enforce All / Adopt All |
| `GET /settings` | — | `list[SettingView]` | editable settings |
| `PUT /settings` | `SettingUpdate` (`key`, `value`, `confirmation`) | `SettingView` | change one setting live |
| `POST /buffer/{entry_id}/revoke` | — | `ActionResult` | close the PR and evict every entry sharing it |
| `GET /whitelist` | — | `list[WhitelistEntry]` | exemptions |
| `POST /whitelist` | `WhitelistRequest` | `WhitelistEntry` | add an exemption |
| `DELETE /whitelist/{entry_id}` | — | `ActionResult` (404 if unknown) | remove an exemption |
| `GET /audit?limit=` | — | `list[AuditLogEntry]` | audit rows, newest first (limit clamped 1–1000) |
| `GET /events?limit=` | — | list of dicts | drift transitions |
| `GET /buffer` | — | `list[PendingAdoption]` | active buffer entries |

### The SSE stream

`GET /api/state/stream` subscribes a queue on the projector and streams:

```
event: snapshot
data: {"version": 12, "snapshot": {...}}            ← immediately on connect

event: snapshot
data: {"version": 13, "snapshot": {...}, "transitions": [...]}   ← after every reconcile cycle

: keepalive                                          ← every 15 s of silence
```

`transitions` lists `{ref, display_name, status, previous, message}` for entries whose status
changed. The generator checks `request.is_disconnected()` on every keepalive and unsubscribes
in a `finally`, so closed tabs do not leak queues. Response headers disable caching and
proxy buffering (`X-Accel-Buffering: no`).

---

## Templates

All pages extend `base.html`, which provides:

* the **navigation bar**: Dashboard, Events Log, State Buffer, Cluster Config; the hostname;
  the 🔔 chime toggle; the **mode badge** (red when `enforce`); the **status pill** coloured by
  `OVERALL_META`; a `v1.0` badge;
* three **banners** that `dashboard.js` shows or hides live: `BASELINE_UNAVAILABLE` (red),
  `DOCKER UNAVAILABLE` (red), "Baseline poll failing, using the last known baseline" (amber);
* a footer with the snapshot time and trigger;
* the bottom-right **toast container** (`#toasts`) for action results;
* the reusable dark **modal** (`#modal-root`) that replaced native `confirm()` / `prompt()`;
* the `window.WARDEN` bootstrap object: the initial snapshot, version, the two meta maps, the
  page name and (on the diff page) the `diffRef`, followed by `dashboard.js`.

| Template | What it shows |
| --- | --- |
| `dashboard.html` | Headline with baseline project / source / SHA and the live indicator; **Enforce All**, **Adopt All**, **🔄 Refresh** buttons; six counters (one per status plus Enforcement: *Manual only*, *Grace window* or *Armed*); the live table (status badge, container with the 12-char id or the hint *declared in Git, not running*, service or `project: x`, image, Docker state, finding = changed fields or message, and the action buttons the record allows); a collapsible list of exempt containers. |
| `diff.html` | Header badges; a banner per status (SHADOW IT, DRIFT, MISSING, PENDING ADOPTION with the PR link, MATCHED); the **Git Baseline** pane and, unless the entry is MISSING, the **Live Observed State** pane, each with a line-number and sign gutter; the field-by-field table (changed rows highlighted with ✱); the large action buttons with one-line explanations: *Enforce Baseline (Rollback)* or *Enforce (Spin Up)*, *Adopt Deletion (Auto-PR)*, *Enforce Kill*, *Adopt into Git (Auto-PR)*, *Whitelist container*, *Whitelist project*. A MISSING entry gets a single-column layout with only the baseline pane. |
| `events.html` | 🔄 Refresh; the drift transitions table (time, entry link, `previous → current` badges, details); the audit log table (time, action coloured by kind, target, host, details). |
| `buffer.html` | Explanation of the buffer and the TTL rule; a four-step strip (drift → Adopt → buffered → merged); active entries (entry, kind `adopt` / `remove service`, service, PR link + branch, PR state coloured, created, **Revoke Buffer**); history with outcomes (`baseline_updated`, `pr_merged`, `expired`, `pr_closed`, `revoked`, `superseded`, or `active`). |
| `config.html` | **Runtime settings** rows (label, env var, widget by `kind`, help, `override` badge, Save, Reset); the effective configuration list; the raw baseline YAML with SHA and check time; the whitelist card with an add form (type, identifier, reason) and per-entry **Revoke**; four database count tiles. |

Templates render the initial state server-side; after that `dashboard.js` re-renders the
parts that change from SSE payloads, so a page works even if JavaScript never connects.

---

## `static/js/dashboard.js`

One IIFE, no framework, no build step. Sections in file order:

**Toasts and flash.** `toast(message, tone)` appends to `#toasts` bottom-right for 6 s.
`flash()` stores a toast in `sessionStorage` under `warden.flash` so it can be shown after a
redirect (`redirectHome`), and `showFlash()` displays it on the next page load.

**Chimes.** Web Audio, no audio files. `playDriftWarning` is a descending dual tone, played
when `overall` leaves `in_sync`; `playResolution` is an ascending arpeggio, played when it
returns. The 🔔 button mutes and persists the choice in `localStorage` (`warden.chimes`).
Browsers block audio until the first user gesture, so the `AudioContext` is created lazily on
the first click.

**Modal.** `wardenModal({title, message, bodyHtml, inputs, confirmText, danger, radios})`
returns a Promise resolving to the entered values or `null` on cancel. It drives every
confirmation, the optional PR note, the `ENABLE AUTONOMOUS` phrase, the whitelist reason and
the batch scope picker. `Escape` and the backdrop cancel.

**Impact preview.** `impactHtml(action, record)` renders what Revert / Kill will do in Docker
terms (which container is removed, which image and name are created, which fields change)
inside the modal, using the record from the current snapshot.

**Rendering.** `renderChrome(snapshot)` updates the status pill, mode badge, banners, footer
and counters on every page. `renderDashboard(snapshot)` rebuilds the table rows (`rowHtml`)
and the exempt list. `renderDiffPage(snapshot)` watches the entry the diff page shows: if it
vanished from the snapshot the page redirects to `/` with a flash; if its status changed the
page reloads (or navigates to the new `ref` if the identifier changed, for example after a
MISSING service was spun up).

**SSE.** `connect()` opens `EventSource("/api/state/stream")`, applies each payload and flips
the `#live-indicator` between *live* and *reconnecting…*. The browser reconnects on its own.
The first payload does not chime (`announce=false`).

**Actions.** `post(url, body, method)` is the fetch helper; it throws with the server's
`message` (409) or `detail` (422) so toasts show the real reason. The `ACTIONS` table holds the
modal copy per action (`revert`, `kill`, `adopt`, `adopt_deletion`), and `wardenAction` runs:
modal → `POST /api/actions/<endpoint>/<ref>` → toast. Revert and Kill redirect to the
dashboard with a flash; Adopt stays on the page and the PR link appears on the entry.

**Global handlers** (called from inline `onclick` in the templates):

| Function | Calls | Notes |
| --- | --- | --- |
| `wardenAction(action, ref)` | `POST /api/actions/{revert,kill,adopt,adopt-deletion}/{ref}` | confirmation modal, impact preview for destructive ones |
| `wardenBatch(operation)` | `POST /api/actions/batch` | scope modal (All workloads always; rogue / drifted / missing only when non-empty) with a live "affected" list; toasts `succeeded` / `failed` counts and the batch PR link |
| `wardenSaveSetting(key)` / `wardenResetSetting(key, default)` | `PUT /api/settings` | asks for `ENABLE AUTONOMOUS` when the mode goes to `enforce`; reload on success |
| `wardenWhitelist(type, identifier)` / `wardenWhitelistForm(event)` | `POST /api/whitelist` | reason modal or the Config form |
| `wardenUnwhitelist(id, identifier)` | `DELETE /api/whitelist/{id}` | |
| `wardenRevokeBuffer(id, name, prNumber)` | `POST /api/buffer/{id}/revoke` | modal warns that every entry sharing the PR is evicted |
| `wardenReconcile()` / `wardenRefreshPage()` | `POST /api/reconcile` | dashboard refresh (SSE repaints) / Events page refresh (reloads after the cycle) |
| `wardenModal(options)` | — | exported for reuse |

## `static/css/style.css`

Small and hand-written on top of the Tailwind CDN build: `.badge`, `.banner`, `.status-pill`
with the pulsing dot, the `tone-*` colour families (emerald, amber, sky, rose, violet, orange,
slate) used by badges, pills and toasts; `.btn` / `.btn-lg` with `-ghost`, `-rose`,
`-emerald`, `-violet` variants; `.code-pane` with the fixed `.ln` / `.sign` gutter and `.add`
/ `.del` row colours (colour depends only on the line marker); `.toast`; the
`.modal-backdrop` / `.modal` / `.modal-input` / `.modal-radio` / `.impact` styles.

---

## Request flow for one button, end to end

Clicking **Revert** on a drifted cache:

1. `wardenAction("revert", "infrastructure-test-cache-1")` opens the modal with the impact
   preview built from `window.WARDEN.snapshot`.
2. On confirm, `POST /api/actions/revert/infrastructure-test-cache-1`.
3. The route awaits `reconciler.revert(ref)`: audit, remember the action, recreate through
   `DockerWatcher`, audit, schedule a `revert` cycle, return an `ActionResult`.
4. The route returns 200 with the result; the page flashes the message and redirects to `/`.
5. The scheduled cycle publishes a new snapshot; the SSE stream pushes it; `renderDashboard`
   repaints the row as `Matched`; `chimeForTransition` plays the resolution arpeggio if the
   host just went back to `IN SYNC`.
