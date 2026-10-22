# `src/warden` — the engine

`src/warden` is the part of DriftWarden that does the work: it fetches the baseline from
GitHub, watches the Docker socket, classifies every container, enforces or adopts, and keeps
the current host snapshot in memory. It runs **inside the FastAPI process** (started from the
application lifespan, see [`web.md`](web.md#apppy)), not as a separate daemon, and it never
imports anything from `web`. The one thing it needs from the web side, the SQLite store, it
reaches through a `Protocol` so the dependency arrow only points one way.

```
src/warden/
├── config.py                  WardenSettings: every environment variable, validated once
├── core/
│   ├── baseline_parser.py     Compose YAML → canonical Baseline (ruamel round-trip)
│   └── docker_watcher.py      docker-py: event stream, inspect, stop/remove, recreate
├── engine/
│   ├── diff_engine.py         pure classification: containers + baseline → DriftRecords
│   ├── state_projector.py     holds the latest HostSnapshot, fans it out over SSE
│   ├── store.py               StateStore Protocol (implemented by web.db.repository.Store)
│   └── reconciler.py          the orchestrator: loops, cycles, enforcement, operator actions
└── automation/
    ├── github_client.py       PyGithub: baseline fetch with ETag, PR open/close/state
    └── yaml_mutator.py        comment-preserving add / update / remove of a service block
```

A one-paragraph tour of a cycle: a Docker event lands on an `asyncio.Queue` (docker_watcher)
→ the reconciler waits for the debounce window → it lists every container (docker_watcher)
→ `diff_engine.evaluate` turns containers + baseline + whitelist + buffer into `DriftRecord`s
→ a `HostSnapshot` is published through the projector (SSE to browsers) and status transitions
are written to SQLite → if enforcement is armed, the reconciler kills rogue containers and
recreates drifted / missing services, then schedules another cycle to confirm.

---

## `config.py`

`WardenSettings` is a `pydantic-settings` `BaseSettings`. It is the **only** place that reads
environment variables; everything else receives a settings object. Values come from the
process environment first, then `.env` in the working directory (`.env.example` documents
every key). `get_settings()` builds it once and caches it with `lru_cache`.

Behaviours worth knowing:

* **Blank strings are rejected** for the string fields (`target_repo`, `database_url`, ...): a
  whitespace-only value is a validation error rather than a silently empty setting.
* **GitHub is mandatory** unless `ALLOW_MOCK_BASELINE=true`. Without the mock flag, missing
  `GITHUB_PAT`, `TARGET_REPO` or `TARGET_BRANCH` fails startup with a clear message. The PAT is
  a `SecretStr`, so `str(settings)` and logs never print it.
* `validate_assignment=True` means `setattr(settings, "warden_mode", "enforce")` re-validates
  the value. This is what lets the Cluster Config page change settings live and still get
  Pydantic validation (see [Runtime settings](#runtime-settings-and-sqlite-overrides)).
* `warden_mode` and `environment` are `Literal` types, so only `observe` / `enforce` and
  `development` / `production` are accepted.

Convenience properties: `hostname` (`WARDEN_HOSTNAME` or the machine hostname),
`excluded_prefixes` (the comma-separated `EXCLUDED_NAME_PREFIXES` split into a tuple),
`enforce` (`warden_mode == "enforce"`).

| Attribute | Env var | Default | Used by |
| --- | --- | --- | --- |
| `github_pat`, `target_repo`, `target_branch`, `baseline_file_path` | `GITHUB_PAT`, `TARGET_REPO`, `TARGET_BRANCH`, `BASELINE_FILE_PATH` | —, —, —, `docker-compose.yml` | `GitHubClient` |
| `baseline_poll_interval_seconds` | `BASELINE_POLL_INTERVAL_SECONDS` | 30 (min 5) | baseline poll loop **and** the pull-request poll loop |
| `allow_mock_baseline`, `mock_baseline_path` | `ALLOW_MOCK_BASELINE`, `MOCK_BASELINE_PATH` | false, `tests/fixtures/docker-compose.test.yaml` | reconciler; adoption is refused in mock mode |
| `compose_namespace` | `COMPOSE_NAMESPACE` | — | fallback project name when the baseline has no `name:` |
| `docker_socket_path`, `docker_base_url` | `DOCKER_SOCKET_PATH`, `DOCKER_BASE_URL` | `/var/run/docker.sock`, — | `DockerWatcher.connect` |
| `docker_event_reconnect_delay` | `DOCKER_EVENT_RECONNECT_DELAY` | 5 | event pump reconnect sleep |
| `warden_mode` | `WARDEN_MODE` | `observe` | enforcement gate |
| `drift_debounce_seconds` | `DRIFT_DEBOUNCE_SECONDS` | 2 | event loop quiet window |
| `reconcile_sweep_interval_seconds` | `RECONCILE_SWEEP_INTERVAL_SECONDS` | 60 (min 5) | periodic full reconcile |
| `enforcement_grace_seconds` | `ENFORCEMENT_GRACE_SECONDS` | 30 | suppress enforcement after a baseline or mode change |
| `adoption_buffer_ttl_seconds` | `ADOPTION_BUFFER_TTL_SECONDS` | 600 | safety TTL for buffer entries whose PR cannot be followed |
| `excluded_name_prefixes` | `EXCLUDED_NAME_PREFIXES` | `warden-,driftwarden,k8s_` | hard exclusions |
| `api_host`, `api_port`, `environment`, `log_level` | `API_HOST`, `API_PORT`, `ENVIRONMENT`, `LOG_LEVEL` | `127.0.0.1`, 8000, `development`, `INFO` | Dockerfile CMD, logging |
| `warden_hostname` | `WARDEN_HOSTNAME` | machine hostname | UI and audit rows |
| `database_url` | `DATABASE_URL` | `sqlite:///./warden.db` | `web.db.session.Database` |

---

## `core/baseline_parser.py`

Turns the text of a `docker-compose.yml` into a `Baseline`, with every service in the
canonical shape described in [`shared.md`](shared.md#dataclasses-engine-internal). The diff
engine and the recreate path only ever see this canonical form, so the file can use short or
long Compose syntax freely.

**`Baseline`** (dataclass): `project_name`, `services` (`dict[str, ParsedService]` in document
order), `raw_yaml` (kept for the Cluster Config page and for adoption mutations), `sha`
(Git blob SHA, or a SHA-1 digest of the file in mock mode), `source`, `fetched_at`,
`declared_networks` and `declared_volumes` (top-level keys, used when recreating).

Functions, in the order parsing uses them:

| Function | What it does |
| --- | --- |
| `new_yaml()` | A `ruamel.yaml` round-trip instance (`typ="rt"`, quotes preserved, 2/4/2 indentation, no line wrapping). Every YAML load and dump in the project goes through this so comments and formatting survive. |
| `load_document(raw)` | Parses text into a `CommentedMap`; raises `BaselineParseError` if the YAML is invalid or the root is not a mapping. |
| `dump_yaml(data)` | Serialises back to text. |
| `interpolate(value, environ)` | Resolves `${VAR}`, `${VAR:-default}`, `${VAR-default}`, `$VAR` and `$$` the way Compose does. Missing variables become empty strings. `environ` defaults to the process environment. |
| `normalise_mapping(node, environ, lookup_missing)` | `labels:` / `environment:` in either list (`KEY=value`) or mapping form → `dict[str, str]`. With `lookup_missing=True` (used for `environment`) a bare `KEY` is read from the variable source. |
| `normalise_port(entry, environ)` | Short (`"8080:80"`, `"127.0.0.1:8080:80/udp"`, `"80"`) or long form → `[host_ip:]host:container/proto`. Equal-length ranges (`8080-8081:80-81`) expand to one entry per port. |
| `normalise_volume(entry, environ)` | Short or long form → `source:target:mode`. Handles Windows drive-letter sources. |
| `normalise_networks(node, environ)` | List or mapping form → list of names. |
| `normalise_service(name, node, environ)` | Builds a `ParsedService` from one `services:` entry using the helpers above. |
| `resolve_project_name(document, fallback)` | The document's top-level `name:` wins; otherwise `COMPOSE_NAMESPACE`; otherwise `ConfigurationError`. This name is the **managed namespace**: containers whose `com.docker.compose.project` label equals it are evaluated as managed services. |
| `parse_baseline(raw, fallback_namespace, sha, source, environ)` | The entry point: load, require a `services:` mapping, resolve the project name, normalise every service, collect declared networks and volumes. |
| `service_to_mapping(service)` / `render_service_yaml(service)` | Render a canonical service back to a Compose-style mapping / YAML text rooted at the service name. Used for both panes of the diff page. Only non-empty keys are written. |

---

## `core/docker_watcher.py`

The only module that talks to Docker, through `docker-py`. Two kinds of code live here: pure
converters (testable without Docker) and the `DockerWatcher` class that owns the client.

### Data types and converters

* **`MountInfo`** — one mount (`kind` bind/volume/tmpfs, `source`, `target`, `read_only`).
  `canonical(project)` renders `source:target:mode`, strips the `<project>_` prefix from named
  volumes and blanks the source of anonymous (64-hex-char) volumes so it compares to the
  baseline form.
* **`LiveContainer`** — a fully inspected container: `id`, `name`, `image` (reference as
  written), `image_id`, `state`, `created`, `labels`, `environment` (from `Config.Env`),
  `ports` (canonical), `mounts`, `restart` (`no`, `always`, `on-failure:3`, ...), `networks`
  (sorted names), `command`. Properties `compose_project`, `compose_service`, `short_id`,
  and `volumes(project)` (canonical strings for bind and volume mounts only).
* **`container_from_attrs(attrs)`** — builds a `LiveContainer` from `docker inspect` JSON.
  Port bindings on `0.0.0.0` / `::` lose their host IP so they match the baseline's
  `host:container/proto` form.
* **`detect_self_container_id()`** — reads `/proc/self/mountinfo`, `/proc/self/cgroup` or
  `/proc/1/cpuset` for a 64-character id. When DriftWarden runs as a container this lets it
  exclude itself.
* **`STATE_CHANGING_EVENTS`** — `create`, `start`, `restart`, `die`, `stop`, `kill`, `oom`,
  `pause`, `unpause`, `destroy`, `rename`, `update`. Only these event actions reach the engine;
  `exec_*`, `attach`, health checks and the like are dropped on the worker thread.
* **`ports_to_docker`, `restart_policy_to_docker`, `volumes_to_docker`** — convert canonical
  strings back into the keyword arguments `docker-py` wants for `containers.create`. Named
  volumes are prefixed with the project (`<project>_data`); relative bind sources (`./data`)
  are skipped with a warning because the engine has no Compose directory to resolve them against.

### `DockerWatcher`

Connection: `DOCKER_BASE_URL` if set, else `unix://<DOCKER_SOCKET_PATH>` if that path exists,
else `docker.from_env()` (which honours `DOCKER_HOST`; this is the path on Windows). The client
is created lazily on first use and `ping()`ed; any `DockerException` becomes
`DockerConnectionError`. After a failure `_reset` drops the client so the next call reconnects.

| Method | Behaviour |
| --- | --- |
| `list_containers()` | `containers(all=True)` then `inspect_container` on each. Stopped containers are included (they become MISSING). DriftWarden's own container is skipped. A container removed between list and inspect is ignored. |
| `inspect(id_or_name)` | One container or `None`. |
| `image_default_environment(image)`, `image_default_labels(image)` | The `Env` / `Labels` baked into an image. The diff engine subtracts these so only operator-set values are diffed or adopted. Empty dict on any failure. |
| `stop_and_remove(id, timeout=10)` | Graceful `stop`, forced `remove` if that fails; already-gone containers are not an error. Raises `RogueContainerKillError`. |
| `recreate(service, project, action_id, declared_volumes)` | Enforce / Revert / Spin up. Steps: refuse if the service has no image; remove any container with the target name (`container_name` or `<project>-<service>-1`); pull the image if absent; build labels = service labels + the four Compose labels + `warden.managed-by` + `warden.action-id`; ensure each `<project>_<network>` exists (creating a bridge network with Compose labels if not, defaulting to `<project>_default`); `containers.create(...)` with converted ports, environment, volumes, restart policy, first network and `hostname=<service>`; connect extra networks; `start()`. Returns the new id. Raises `ContainerRecreateError`. |
| `pump_events(queue, stop_event)` | Async. Runs `_stream_events` on a worker thread via `run_in_executor` and restarts it after `DOCKER_EVENT_RECONNECT_DELAY` whenever it dies, forever, until `stop_event` is set. |
| `_stream_events(...)` | Blocking thread body: `client.events(decode=True, filters={"type": "container"})`, keep only `STATE_CHANGING_EVENTS`, hand each `RawDockerEvent` to the loop with `call_soon_threadsafe(queue.put_nowait, event)`. |
| `close()` | Closes the stream and the client. |

Everything except `pump_events` is synchronous; the reconciler wraps calls in `asyncio.to_thread`.

---

## `engine/diff_engine.py`

Pure functions: no Docker, GitHub or database. Input is a list of `LiveContainer`s and an
`EvaluationContext`; output is an `Evaluation` with `records` (the dashboard rows) and
`exempt` (skipped containers with reasons). This is where every classification rule lives,
and it is the most heavily unit-tested module.

**`EvaluationContext`** carries the baseline (or `None`), hostname, excluded prefixes, the
engine's own container id, a `WhitelistIndex` (sets of whitelisted container ids/names and
project names, rebuilt from SQLite), the active adoption buffer keyed by container id (or
`service:<name>` for deletion entries), and two lookup callables for image-default
environment and labels (the reconciler passes cached `DockerWatcher` lookups).

### Evaluation order (`evaluate`)

1. **Hard exclusions** (`exclusion_reason`): DriftWarden's own container, `warden.ignore=true`,
   any name prefix in `EXCLUDED_NAME_PREFIXES`, then the whitelist (`WhitelistIndex.matches`:
   exact id or name, a whitelisted id prefix of 12+ characters, or the Compose project).
   Excluded containers go to `exempt` and are never classified.
2. **No baseline loaded** → no records at all (the UI shows the `BASELINE_UNAVAILABLE` banner).
3. **Bucket by project label**:
   * label equals the baseline project → `map_to_service`: the `com.docker.compose.service`
     label if it names a declared service (a label naming an undeclared service is rogue:
     *labelled for the managed project but not declared*); otherwise the name pattern
     `<project>[-_]<service>[-_]<n>`; otherwise an explicit baseline `container_name`.
   * another project label → rogue, *belongs to untracked Compose project*.
   * no label → rogue, *no Compose project labels*.
4. **One record per baseline service** (document order): no container → `_managed_record`
   with `None` (MISSING, or PENDING_ADOPTION if a deletion PR is buffered under
   `service:<name>`); several containers → `_pick_primary` keeps the newest running one (or
   the newest at all) and marks the rest rogue as *duplicate replica*.
5. **Rogue records** are appended after the managed ones. A rogue container with an active
   buffer entry is re-labelled `PENDING_ADOPTION` with no actions.

### Managed record rules (`_managed_record`)

| Condition (checked in this order) | Status | Actions |
| --- | --- | --- |
| no container | `MISSING` | `revert` (Spin up), `adopt_deletion` |
| container state in `MISSING_STATES` = `exited`, `created`, `removing` | `MISSING` | `revert`, `adopt_deletion` |
| active buffer entry for the container | `PENDING_ADOPTION` | none |
| any `FieldDiff.changed` (including the synthetic `state` row for `paused`, `restarting`, `dead`) | `DRIFTED` | `revert`, `adopt` |
| otherwise | `MATCHED` | none |

Rogue records always get `adopt`, `kill`, `whitelist`, plus `whitelist_project` when the
container carries a project label. The record's `ref` is the container name, or the service
name when nothing is running.

### Field comparison (`compare`)

One `FieldDiff` per entry of `DIFF_FIELDS`:

| Field | Rule |
| --- | --- |
| `image` | Both sides through `normalise_image`: strip `docker.io/`, `docker.io/library/`, `index.docker.io/...`; add `:latest` when no tag; digest references untouched. Compared as `repo:tag`. |
| `ports` | Set comparison of canonical strings. A baseline entry with no host port (`80/tcp`) matches any published host port for that container port. Extra published ports on the container count as drift. |
| `environment` | Only keys declared in the baseline are compared; a missing key shows as `<unset>`. Extra live variables are ignored here (they do show in the observed YAML). |
| `volumes` | Baseline-declared volumes, matched by target path. Source is compared only when the baseline source is not relative; mode (`ro`/`rw`) always. |
| `labels` | Only baseline-declared labels; Compose and warden labels never count. |
| `restart` | Baseline value or `no` versus the live policy. |
| `networks` | Sorted names after stripping the `<project>_` prefix; baseline default is `["default"]`. |
| `state` (extra row) | Added when the container is not `running`. |

### Observed service (`observed_service`)

Renders a live container as the Compose service it *would* be declared as, for the right-hand
diff pane and for adoption:

* `labels` = container labels minus Compose/warden labels minus labels inherited from the image.
* `environment` = variables not inherited from the image, with **every value replaced by
  `${KEY}`**. Secrets are never rendered on screen or written to Git.
* `volumes` without anonymous ones, `networks` without `default`/`bridge`/`host`/`none`,
  `restart` omitted when `no`, no `container_name`.

---

## `engine/state_projector.py`

The in-memory home of the latest `HostSnapshot` and the fan-out point for live updates.

* `snapshot` — the current snapshot (starts as an empty one with `BaselineSource.UNAVAILABLE`
  so pages can render before the first cycle).
* `record_key(record)` — the identity used to compare cycles: `service:<name>` for managed
  entries, `container:<id>` for rogue ones.
* `transitions(new)` — records whose status differs from the previous snapshot, as
  `(record, previous_status)` pairs; brand-new entries count only if they are not `MATCHED`.
* `publish(snapshot, transitions)` — swaps the snapshot, bumps `version`, and puts one JSON
  message (`{"version", "snapshot", "transitions"}`) on every subscriber queue. Queues hold
  32 messages; a slow browser just loses updates rather than blocking the engine.
* `subscribe()` / `unsubscribe(queue)` — used by the SSE route.
* `snapshot_payload(snapshot)` — the JSON shape sent to browsers: the model dump plus
  `counts` and `overall`, minus `baseline.raw_yaml`.

---

## `engine/store.py`

`StateStore` is a `typing.Protocol` listing the persistence calls the reconciler makes:
`record_audit`, `add_pending`, `active_pending`, `set_pending_state`, `clear_pending`,
`list_whitelist`, `add_whitelist`, `remove_whitelist`, `list_settings`, `set_setting`,
`delete_setting`, `record_drift_event`. The real implementation is
`web.db.repository.Store`; tests could pass anything with the same methods. All methods are
synchronous and the reconciler calls them through `asyncio.to_thread`.

---

## `engine/reconciler.py`

The orchestrator. One `Reconciler` is created in the FastAPI lifespan with the settings, the
`DockerWatcher`, the store, the `StateProjector` and a `GitHubClient` (or `None` in mock mode).

### Lifecycle

`start()` runs, in order:

1. `_reload_durable_state` — rebuild the whitelist index and the adoption buffer from SQLite.
2. `_apply_stored_settings` — apply `setting_overrides` rows on top of the environment.
3. `refresh_baseline(force=True)` — first fetch. A failure here is logged and audited but does
   **not** stop startup: the engine runs in degraded observe mode until a poll succeeds.
4. `reconcile("startup")` — the first pass. Enforcement is **always** skipped on the startup
   trigger, so a restart in enforce mode never takes destructive action before an operator
   has seen the dashboard.
5. `startup_done = True`, audit `ENGINE_STARTED`, then five background tasks:

| Task | What it does |
| --- | --- |
| `docker-events` | `watcher.pump_events` feeding the queue. |
| `event-consumer` | `_event_loop`: debounce and reconcile on `event`. |
| `baseline-poller` | every `BASELINE_POLL_INTERVAL_SECONDS`: `refresh_baseline()`. |
| `sweep` | every `RECONCILE_SWEEP_INTERVAL_SECONDS`: `reconcile("sweep")`, a safety net for events missed during reconnects. |
| `pull-requests` | every `BASELINE_POLL_INTERVAL_SECONDS`: `poll_pull_requests()`. |

`stop()` sets the stop event, closes the watcher and cancels the tasks. Every loop body is
wrapped so an exception is logged and the loop continues.

### Baseline handling

* `refresh_baseline(force)` picks `_load_github_baseline` (ETag conditional request; `304`
  means nothing changed and only `fetched_at` is refreshed) or `_load_mock_baseline` (SHA-1
  digest of the fixture file; the file is re-read every poll, so editing the fixture while
  running in mock mode is a cheap way to simulate a baseline change). Errors
  (`GitBaselineFetchError`, `BaselineParseError`, `ConfigurationError`, `OSError`) keep the
  last known baseline, set `baseline_error` (shown in the amber "poll failing" banner) and
  write one `BASELINE_FETCH_FAILED` audit row per distinct message.
* `_install_baseline` swaps the baseline in only when the SHA changed, records
  `BASELINE_FETCHED`, and stamps `_baseline_changed_at` which starts the **enforcement grace
  window**.
* `_on_baseline_changed` clears buffer entries the new baseline satisfies (an `adopt` entry
  whose service is now declared, a `remove` entry whose service is now gone) with
  `ADOPTION_CLEARED`, then schedules a reconcile.
* `baseline_info()` builds the `BaselineInfo` for snapshots.

### The event loop and debounce

`_event_loop` takes one event from the queue, ignores it if it was caused by DriftWarden's own
action (see below), then keeps draining the queue until `DRIFT_DEBOUNCE_SECONDS` pass with no
new event, and runs a single `reconcile("event")`. The window is global, not per container:
`docker compose up` of a ten-service stack causes one cycle, not ten.

### Self-action cache

Every destructive operation first calls `_remember_action(container_id, name, target_name,
action_id)`; the keys live for `ACTION_CACHE_TTL_SECONDS` (20 s). `_is_own_action(event)`
checks the event's container id, name and `warden.action-id` label against the cache. Without
this, a Revert would trigger `die`/`destroy`/`create`/`start` events and a pointless extra
cycle (or, in enforce mode, an enforcement loop).

### One cycle (`reconcile(trigger)`)

Under `_cycle_lock` (cycles never overlap):

1. `_expire_pending` — drop buffer entries that hit the safety TTL (below).
2. `watcher.list_containers()` in a thread. On `DockerConnectionError` the cycle continues
   with no containers and `docker_connected=False`; the snapshot then has no records and the
   red "DOCKER UNAVAILABLE" banner shows.
3. `evaluate(containers, self._context())` in a thread (it may touch Docker for image lookups,
   which are cached per image id).
4. Build the `HostSnapshot`, compute `transitions`, `projector.publish`.
5. `_persist_transitions` — one `drift_events` row per transition, plus an audit row:
   `ROGUE_DETECTED` / `DRIFT_DETECTED` / `MISSING_DETECTED` for problems, `STATE_RESTORED`
   when an entry returns to `MATCHED`.

Then, outside the lock: if `enforcement_active` and the trigger is neither `startup` nor
`post-enforce`, run `_enforce(snapshot)`; if it acted, schedule `reconcile("post-enforce")` so
the dashboard shows the result (and so that cycle itself does not enforce again).

### Enforcement rules (enforce mode only)

`enforcement_active` is true only when all of: `WARDEN_MODE=enforce`, startup finished, a
baseline is loaded, and `ENFORCEMENT_GRACE_SECONDS` have passed since the last baseline change
or since the mode was switched to enforce.

`_enforce` then:

* collects ROGUE, DRIFTED and MISSING records (PENDING_ADOPTION is exempt by construction);
* **re-fetches the baseline** (`force=True`, except in mock mode) and abandons the pass if it
  changed, so it never acts on stale truth;
* skips any entry it acted on less than `ENFORCEMENT_COOLDOWN_SECONDS` (60 s) ago and is
  still broken, logging a warning: this is the loop guard for a service that cannot be fixed
  (for example an image that will not pull);
* kills rogue containers and recreates drifted / missing services, auditing each outcome.
  Failures are logged and the pass continues with the next entry.

### Operator actions

All of these take a `ref` and resolve it with `HostSnapshot.find`. They raise
`DriftWardenError` (→ HTTP 409) when the action does not apply.

| Method | Applies to | What happens |
| --- | --- | --- |
| `revert(ref)` | DRIFTED or MISSING managed service (not PENDING_ADOPTION) | Audit `REVERT_REQUESTED`; remember the action; if the live container has a different name than the target, remove it; `watcher.recreate` from the baseline service; audit `REVERT_COMPLETED` or `REVERT_FAILED`; schedule `reconcile("revert")`. |
| `kill(ref)` | ROGUE (or a MISSING entry that still has a stopped container) | Remember; `stop_and_remove`; audit `ENFORCE_KILL` / `ENFORCE_KILL_FAILED`; reconcile. |
| `adopt(ref, note)` | ROGUE or DRIFTED | See below. |
| `adopt_deletion(ref, note)` | MISSING | Opens a PR that deletes the service block; buffers under `service:<name>` with `kind="remove"`. |
| `batch(request)` | a scope | Audit `BATCH_REQUESTED`; `enforce` runs kill/revert per target, collecting a result per entry; `adopt` delegates to `adopt_all`. |
| `revoke_adoption(entry_id)` | an active buffer entry | Below. |
| `whitelist(request)` / `unwhitelist(id)` | — | Write the row, audit, rebuild the index, reconcile. |

**Adoption (`adopt`)** — the Reverse-GitOps path:

1. `_plan_adoption` inspects the container fresh (not the snapshot), derives the service name
   (`record.service_name` for drifted, else `sanitise_service_name(container.name, project)`),
   builds the observed `ParsedService`, and from it an `AdoptionSpec`: image, ports, custom
   labels, restart, networks (with networks belonging to other projects marked external), and
   **only the environment keys**, never values. `is_update` is true for DRIFTED (sync the
   changed fields) and false for ROGUE (add a new service).
2. A `mutate(latest_text)` closure applies `update_service` or `add_service` to whatever the
   file contains on GitHub at that moment.
3. Audit `ADOPT_REQUESTED`; build the title (`warden: adopt <container> as service <name>` /
   `warden: sync <service> with live container <name>`) and the Markdown body (`_pr_body`:
   host, container, image, state, synced fields with `expected → observed`, the `${KEY}`
   list, the operator note).
4. `github.open_adoption_pr(...)`. An open PR for the same container is reused.
5. `store.add_pending(...)` with the PR number, URL and branch; the entry goes into
   `_pending`; audit `ADOPT_PR_OPENED`; reconcile. The entry now shows as `PENDING_ADOPTION`.

**Adopt All (`adopt_all`)** — every target of the batch (rogue → add, drifted → sync,
missing → remove) is applied in **one** `mutate` closure on **one** branch
(`warden/batch-adopt-<timestamp>`) and **one** PR whose body is a checklist. Each adopted
container gets its own buffer entry, all pointing at that PR. If GitHub refuses the PR, every
target gets a failed `ActionResult` and nothing is buffered. Mock mode refuses the whole batch.

**Buffer lifecycle** — three things can end a `PENDING_ADOPTION` entry:

| Trigger | Outcome column | Audit | Result |
| --- | --- | --- | --- |
| `poll_pull_requests` sees the PR **merged** | `pr_merged` / `baseline_updated` | `ADOPTION_MERGED`, then `ADOPTION_CLEARED` once the re-fetched baseline declares (or no longer declares) the service | entry gone, workload `MATCHED` |
| `poll_pull_requests` sees the PR **closed** unmerged | `pr_closed` | `ADOPTION_CLOSED` | entry gone, workload back to ROGUE / DRIFTED / MISSING |
| operator clicks **Revoke Buffer** (`revoke_adoption`) | `revoked` | `ADOPTION_REVOKED` per entry | the PR is commented on and closed; **every** entry sharing that PR number is evicted (a batch PR revokes the whole batch); if GitHub refuses to close the PR the entries are evicted anyway and the audit row says so |
| `_expire_pending` safety TTL | `expired` | `ADOPTION_EXPIRED` | only for entries with **no** PR number (`expires_at` passed) or whose PR has been **unreachable** for `ADOPTION_BUFFER_TTL_SECONDS`; an entry with a reachable open PR never expires, however long review takes |

`poll_pull_requests` asks GitHub once per distinct PR number per cycle, records the state on
the row (`pr_state`), and only re-fetches the baseline when at least one PR merged. A GitHub
error leaves entries untouched and starts the "unreachable since" clock.

### Runtime settings and SQLite overrides

`EDITABLE_SETTINGS` lists what the Cluster Config page may change: `warden_mode`,
`drift_debounce_seconds`, `baseline_poll_interval_seconds`,
`reconcile_sweep_interval_seconds`, `enforcement_grace_seconds`,
`adoption_buffer_ttl_seconds`, `excluded_name_prefixes`. For each, `setting_views()` reports the
effective value, the environment default and whether an override is active.

`apply_setting(update)`:

* normalises the value; switching `warden_mode` to `enforce` requires
  `confirmation == "ENABLE AUTONOMOUS"` (checked here, server-side) and re-arms the grace window;
* `setattr` on the settings object (validated by Pydantic; `ConfigurationError` on a bad value);
* if the new value equals the environment default the override row is deleted ("Reset"),
  otherwise it is written to `setting_overrides`;
* audit `SETTINGS_CHANGED` with old and new value, update the projector's mode, reconcile.

`.env` is never written. Overrides are re-applied on every start (`_apply_stored_settings`) and
win over the environment; an override that no longer validates is dropped with a warning.

The loops read the settings object each iteration, so a changed interval takes effect on the
next sleep.

---

## `automation/github_client.py`

A thin, stateless wrapper around PyGithub scoped to `TARGET_REPO`. There is no local clone and
no subprocess `git`; every operation is a REST call.

| Method | Behaviour |
| --- | --- |
| `verify_access()` | Checks the token and the repository; returns the login. |
| `fetch_baseline(etag)` | `GET /repos/{repo}/contents/{path}?ref={branch}` through PyGithub's requester with `If-None-Match`. `304` → `None` (unchanged, no quota used). Otherwise decodes the base64 content and returns `FetchedBaseline(content, sha, etag)`. |
| `pull_state(number)` | `open`, `merged` or `closed`. |
| `close_pull(number, comment)` | Posts the comment and closes the PR if it is still open. |
| `find_open_adoption_pr(container_name)` | Scans open PRs against the base branch for a head branch starting with `warden/adopt-<slug>-`. |
| `open_adoption_pr(container_name, service_name, mutate, title, body, branch_prefix, reuse_open)` | Reuse an open PR if asked and one exists; otherwise: read the base branch SHA, create `refs/heads/<prefix><slug>-<UTC timestamp>`, read the **current** file on that branch, call `mutate(text)`, `update_file` with commit message `feat(warden): adopt <service> into <path>`, `create_pull`. Any `GithubException` becomes `AdoptionError`. |

Branch names: single adoptions use `warden/adopt-<container-slug>-<timestamp>`, batches use
`warden/batch-adopt-batch-<timestamp>`. `slugify` lowercases and collapses unsafe characters to `-`.

---

## `automation/yaml_mutator.py`

The only code that edits the Compose file, always on a ruamel round-trip AST so the operator's
comments, quoting and key order survive in the pull request.

* **`AdoptionSpec`** — what to write: `service_name`, `image`, optional `container_name`
  (always `None` in practice; generated names are never pinned), `ports` (canonical),
  `networks`, `external_networks` (name → full Docker network name, declared `external: true`),
  `labels`, `restart`, `environment_keys`.
* **`sanitise_service_name(container_name, project)`** — `infrastructure-test-cache-1` with
  project `infrastructure-test` → `cache`; `rogue-db` → `rogue-db`; anything odd →
  `[a-z0-9_.-]` only, fallback `adopted-service`.
* **`unique_service_name(candidate, existing)`** — `cache`, then `cache-adopted`,
  `cache-adopted-2`, ... to avoid clobbering a declared service.
* **`port_to_compose(canonical)`** — `8080:80/tcp` → `"8080:80"`, `/udp` is kept, a
  `0.0.0.0:` prefix is dropped.
* **`add_service(raw, spec)`** → `(text, final_name)`: appends a new service block
  (`image`, `ports` as quoted strings, `environment` as `KEY=${KEY}` lines, `labels` as
  quoted `key=value` lines, `restart` unless `no`, `networks`) and declares any non-default
  networks at the top level.
* **`update_service(raw, spec, fields)`** → `(text, name)`: rewrites only the named fields of
  an existing service; an emptied block is removed rather than left as `[]`. `volumes` is
  deliberately **not** written (logged as skipped): bind paths from a live host are not safe to
  commit.
* **`remove_service(raw, service_name)`** → text with that service block deleted.

All three raise `YAMLMutationError` for a missing `services:` mapping or an unknown service,
which the reconciler turns into `AdoptionError`.

---

## Constants worth remembering

| Constant | Value | Where |
| --- | --- | --- |
| `ACTION_CACHE_TTL_SECONDS` | 20 s | reconciler: how long an own-action key suppresses events |
| `ENFORCEMENT_COOLDOWN_SECONDS` | 60 s | reconciler: minimum gap between two autonomous actions on the same entry |
| `AUTONOMOUS_CONFIRMATION` | `ENABLE AUTONOMOUS` | reconciler: phrase required to switch to enforce at runtime |
| `MISSING_STATES` / `DRIFTED_STATES` | `exited, created, removing` / `paused, restarting, dead` | diff engine |
| `IMPLICIT_NETWORKS` | `default, bridge, host, none` | diff engine: never written to observed YAML |
| `PENDING_REMOVAL_PREFIX` | `service:` | diff engine / reconciler: buffer key for deletion PRs |
| `ADOPTION_BRANCH_PREFIX` / `BATCH_BRANCH_PREFIX` | `warden/adopt-` / `warden/batch-adopt-` | github client |
| `STATE_CHANGING_EVENTS` | see above | docker watcher |
