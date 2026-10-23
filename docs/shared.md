# `src/shared` — the contract between the engine and the web layer

`src/shared` is the smallest package in DriftWarden and the only one that both of the
other packages import. It holds no behaviour: no Docker calls, no GitHub calls, no
database access. It only defines **what the data looks like** when it crosses a boundary.

Three boundaries exist:

| Boundary | Example |
| --- | --- |
| Engine → web | The reconciler publishes a `HostSnapshot`; the dashboard renders it. |
| Web → engine | A route validates an `AdoptionRequest` and hands it to the reconciler. |
| Process → database / HTTP | `AuditLogEntry`, `PendingAdoption`, `ActionResult` are what the API returns. |

Import rules that keep the layering honest:

* `shared` imports nothing from `warden` or `web`.
* `warden` imports `shared`, never `web` (it talks to the database through a Protocol, see
  [`warden.md`](warden.md#enginestorepy)).
* `web` imports both.

The import root is `src` (`PYTHONPATH=src`), so the package is referenced as `shared`, never
`src.shared`.

```
src/shared/
├── __init__.py     re-exports the public names (see the bottom of this page)
├── constants.py    Docker label names, compared fields, the four enums
├── exceptions.py   the DriftWardenError hierarchy
└── models.py       dataclasses for engine-internal records, Pydantic models for everything else
```

---

## `constants.py`

### Docker label names

| Constant | Value | Why it matters |
| --- | --- | --- |
| `COMPOSE_PROJECT_LABEL` | `com.docker.compose.project` | Which Compose project a container claims to belong to. Equal to the baseline's project name ⇒ "managed namespace". |
| `COMPOSE_SERVICE_LABEL` | `com.docker.compose.service` | Which service created the container. First thing the diff engine uses to map a container to a baseline service. |
| `COMPOSE_CONTAINER_NUMBER_LABEL` | `com.docker.compose.container-number` | Replica index. Stamped as `1` when DriftWarden recreates a container. |
| `COMPOSE_ONEOFF_LABEL` | `com.docker.compose.oneoff` | Stamped as `False` on recreated containers so they look like normal service containers. |
| `COMPOSE_LABEL_PREFIX` | `com.docker.compose.` | Every label with this prefix is "internal" and is never diffed or written into an adoption PR. |
| `WARDEN_IGNORE_LABEL` | `warden.ignore` | Opt-out. A container with `warden.ignore=true` is never evaluated (DriftWarden's own image sets it in the Dockerfile). |
| `WARDEN_MANAGED_BY_LABEL` / `WARDEN_MANAGED_BY_VALUE` | `warden.managed-by` = `driftwarden` | Stamped on every container the engine creates. |
| `WARDEN_ACTION_LABEL` | `warden.action-id` | Carries the enforcement action id so the engine can recognise the Docker events caused by its own recreate and not reconcile on them. |

### Other constants

* `DEFAULT_EXCLUDED_NAME_PREFIXES = ("warden-", "driftwarden", "k8s_")` — container name
  prefixes that are never evaluated. This is the default for `EXCLUDED_NAME_PREFIXES`.
* `DIFF_FIELDS = ("image", "ports", "environment", "volumes", "labels", "restart", "networks")`
  — the service attributes the diff engine compares, in the order the diff page shows them.
  A `state` row is appended by the engine when the container is not running; it is not in
  this tuple on purpose.

### Enums

All four enums subclass `str`, so their values serialise to plain strings in JSON and SQLite.

**`DriftStatus`** — the relationship between one entry and the Git baseline.

| Member | Value | Meaning |
| --- | --- | --- |
| `MATCHED` | `matched` | Running container matches its baseline service on every compared field. |
| `ROGUE` | `rogue` | Container exists on the host but is not declared in Git (no labels, foreign project, spoofed labels, or a duplicate replica). |
| `MISSING` | `missing` | Service is declared in Git but has no running container (none at all, or only an exited / created one). |
| `DRIFTED` | `drifted` | Service exists in Git but a compared field differs, or the container is paused / restarting / dead. |
| `AWAITING_RESTART` | An adopted rogue container whose PR merged: declared in Git, still running without Compose labels; `revert` (Enforce) or `docker compose up` turns it into `MATCHED`. |
| `PENDING_ADOPTION` | `pending_adoption` | Buffered while an adoption pull request is open; exempt from enforcement. |

**`AuditAction`** — every row in the `audit_log` table has one of these. Grouped by who writes them:

| Group | Members |
| --- | --- |
| Enforcement | `ENFORCE_KILL`, `ENFORCE_KILL_FAILED`, `REVERT_REQUESTED`, `REVERT_COMPLETED`, `REVERT_FAILED` |
| Adoption | `ADOPT_REQUESTED`, `ADOPT_PR_OPENED`, `ADOPT_FAILED`, `ADOPTION_CLEARED`, `ADOPTION_EXPIRED`, `ADOPTION_MERGED`, `ADOPTION_CLOSED`, `ADOPTION_REVOKED` |
| Baseline | `BASELINE_FETCHED`, `BASELINE_FETCH_FAILED` |
| Detection | `ROGUE_DETECTED`, `DRIFT_DETECTED`, `MISSING_DETECTED`, `STATE_RESTORED` |
| Operator | `WHITELIST_ADDED`, `WHITELIST_REMOVED`, `SETTINGS_CHANGED`, `BATCH_REQUESTED` |
| Lifecycle | `ENGINE_STARTED` |

The Events Log page colours a row by its value: anything containing `failed` is red, anything
containing `detected` is amber, and `revert_completed`, `adopt_pr_opened`, `state_restored` are green.

**`WhitelistTargetType`** — `CONTAINER` (matched by id or name) or `PROJECT` (matched by the
`com.docker.compose.project` label). Stored as the `target_type` column of `whitelisted_entities`.

**`BaselineSource`** — where the cached baseline came from: `GITHUB`, `MOCK`
(`ALLOW_MOCK_BASELINE=true`, loaded from `tests/fixtures/docker-compose.test.yaml`) or
`UNAVAILABLE` (nothing loaded yet; the UI shows the `BASELINE_UNAVAILABLE` banner).

---

## `exceptions.py`

One base class and ten subclasses. Every expected failure in the application inherits from
`DriftWardenError`; the FastAPI app has a single exception handler that converts any of them
into a `409` JSON response of the shape `{"ok": false, "error": "<ClassName>", "message": "..."}`.
Anything that is **not** a `DriftWardenError` is a genuine bug and surfaces as a 500.

```
DriftWardenError
├── ConfigurationError        a required setting is missing or a live setting change is invalid
├── GitBaselineFetchError     GitHub could not be read (token, network, 4xx/5xx, PR lookup)
├── BaselineParseError        the fetched YAML is not a valid Compose document
├── RogueContainerKillError   stop/remove of a container failed
├── ContainerRecreateError    recreate from baseline failed (no image, pull failed, run failed)
├── YAMLMutationError         the Compose AST could not be edited safely (no services:, unknown service)
├── AdoptionError             an adoption could not become a pull request (wraps the two above plus GitHub errors)
├── DockerConnectionError     the Docker daemon is unreachable
└── DatabaseWriteError        a SQLite write failed
```

Where they are raised:

| Exception | Raised by |
| --- | --- |
| `ConfigurationError` | `baseline_parser.resolve_project_name`, `GitHubClient` property access, `Reconciler._assign_setting` / `apply_setting` |
| `GitBaselineFetchError` | `GitHubClient.fetch_baseline`, `verify_access`, `pull_state`, `_repo` |
| `BaselineParseError` | `baseline_parser.load_document`, `normalise_*`, `parse_baseline` |
| `RogueContainerKillError` | `DockerWatcher.stop_and_remove` |
| `ContainerRecreateError` | `DockerWatcher.recreate` |
| `YAMLMutationError` | `yaml_mutator.add_service` / `update_service` / `remove_service` / `_ensure_networks_declared` |
| `AdoptionError` | `GitHubClient.open_adoption_pr` / `close_pull`, the reconciler's adoption paths |
| `DockerConnectionError` | `DockerWatcher.connect` / `list_containers` / `inspect` |
| `DatabaseWriteError` | every write method of `web.db.repository.Store` |
| plain `DriftWardenError` | the reconciler when an operator action does not apply to the entry (for example "Kill" on a matched service, or an unknown ref) |

---

## `models.py`

One rule decides the shape of every type in this file:

> Engine-internal structures are `@dataclass(slots=True)`; anything that crosses the
> engine/web boundary, the database or the HTTP API is a Pydantic model.

Pydantic models all use `ConfigDict(extra="forbid")`, so a typo in a JSON key is a validation
error, and timestamps are `AwareDatetime` (timezone-aware, always UTC). Enum fields keep the
enum type in Python (`use_enum_values=False`) and serialise to the string value in JSON.

### Dataclasses (engine-internal)

**`RawDockerEvent`** (frozen) — one event from the Docker event stream, reduced to
`container_id`, `action` (`start`, `die`, ...) and the string `attributes` Docker attaches
(`name`, `image`, labels). `RawDockerEvent.extract(payload)` builds it from the decoded
`docker.events()` dictionary. The `name` property reads the container name attribute.

**`ParsedService`** — one service from the baseline **after normalisation**. Whatever Compose
syntax the YAML used (short or long form), every collection is stored in one canonical shape:

| Field | Canonical form | Example |
| --- | --- | --- |
| `ports` | `[host_ip:]host_port:container_port/proto`, or `container_port/proto` when not published | `8080:80/tcp` |
| `volumes` | `source:target:mode` (empty source for anonymous volumes, mode `ro` or `rw`) | `data:/var/lib/mysql:rw` |
| `labels`, `environment` | plain `dict[str, str]` | `{"warden.managed": "true"}` |
| `networks` | list of names | `["default"]` |
| `image`, `restart`, `container_name` | strings or `None` | `nginx:alpine` |

The diff engine also builds a `ParsedService` from a **live container** (`observed_service`), so
the same comparison and the same YAML renderer work for both sides of the diff page.

### Pydantic models (boundary contracts)

**`FieldDiff`** — one row of the "Field by field" table: `field`, `expected` (baseline),
`observed` (live), `changed`. Values are already rendered as text.

**`ContainerState`** — a minimal container view (`id`, `name`, `image`, `status`,
`drift_status`). It is exported for API consumers but the current routes do not use it; the
dashboard works from `DriftRecord`.

**`PendingAdoption`** — one row of the `pending_adoptions` table while it is active.

| Field | Notes |
| --- | --- |
| `kind` | `adopt` (add or sync a service) or `remove` (a deletion PR for a MISSING service). |
| `pr_state` | Last state seen on GitHub: `open`, `merged` or `closed`. |
| `container_id` | The Docker id for `adopt`; for `remove` it is the synthetic key `service:<name>`. |
| `pr_number`, `pr_url`, `branch` | The pull request. Entries created by Adopt All share one PR. |
| `created_at`, `expires_at` | `expires_at` only matters when there is no PR to follow (see the TTL rule in `warden.md`). |

**`DriftRecord`** — one row of the dashboard. This is the most important model in the project.

| Field | Meaning |
| --- | --- |
| `ref` | The URL-safe identifier used in `/diff/{ref}` and `/api/actions/*/{ref}`: the container name when a container exists, otherwise the service name. |
| `display_name` | What the table shows: the live container name, else the baseline `container_name`, else the service name. |
| `drift_status` | A `DriftStatus`. |
| `service_name` | Set for managed services; `None` for rogue containers. |
| `project` | The Compose project label of the container, if any. |
| `container_id`, `container_name`, `image`, `status` | From Docker; `status` is the Docker state (`running`, `exited`, ...) or `absent` for MISSING. |
| `diffs` | A list of `FieldDiff`; empty for rogue containers. |
| `expected_yaml`, `observed_yaml` | The two panes of the diff page, already rendered as YAML text. Either may be `None`. |
| `message` | One human sentence, also written to `drift_events.details`. |
| `actions` | Which operator buttons apply: any of `revert`, `kill`, `adopt`, `adopt_deletion`, `whitelist`, `whitelist_project`. The templates and `dashboard.js` only render a button when its name is in this list. |
| `detected_at` | When the record was computed. |
| `pending_adoption` | The buffer entry while the status is `PENDING_ADOPTION`. |

`changed_fields` is a property returning the names of the diffs where `changed` is true.

**`BaselineInfo`** — metadata about the cached baseline: `source`, `available`,
`project_name`, `repo`, `branch`, `path`, `sha`, `fetched_at`, `services` (names), `error`
(last fetch error while the old baseline is still used) and `raw_yaml` (shown on Cluster
Config; stripped from the SSE payload to keep it small).

**`HostSnapshot`** — the full result of one reconcile cycle. The engine keeps exactly one of
these in memory and the whole UI is rendered from it.

| Field | Meaning |
| --- | --- |
| `hostname`, `taken_at`, `trigger` | `trigger` says what started the cycle: `startup`, `event`, `sweep`, `baseline`, `manual`, `revert`, `kill`, `adopt`, `post-enforce`, `settings`, ... |
| `mode`, `enforcement_active` | `observe` / `enforce`, and whether autonomous enforcement is currently armed (mode is enforce, startup is done, a baseline is loaded and the grace window has passed). |
| `docker_connected`, `docker_error` | Whether `list_containers` succeeded this cycle. |
| `baseline` | A `BaselineInfo`. |
| `records` | The `DriftRecord` list, managed services first, then rogue containers. Empty while Docker is unreachable. |
| `exempt` | Human strings like `driftwarden (name prefix driftwarden)` for every skipped container. |

Methods:

* `counts()` → `{"matched": n, "rogue": n, ...}` for the dashboard counters.
* `overall` → the headline status for the navigation pill, in priority order:
  `baseline_unavailable`, `docker_unavailable`, `drift_detected` (any rogue or drifted),
  `missing`, `in_sync`.
* `find(ref)` → a record by `ref`, then by container name / service name / container id, then
  by container id prefix. Every route that takes a `{ref}` goes through this.

**Request models** (validated by FastAPI before the engine sees them):

* `AdoptionRequest` — `container_id` plus an optional `note` (max 1000 chars) for the PR body.
  The adopt routes only use `note`; the target comes from the URL.
* `WhitelistRequest` — `target_type`, `target_identifier` (1–255 chars), `reason`.
* `SettingUpdate` — `key`, `value` (as text; `WardenSettings` validates it), and
  `confirmation`, which must be `ENABLE AUTONOMOUS` when switching the mode to `enforce`.
* `BatchRequest` — `operation` (`enforce` or `adopt`), `scope` (`all`, `rogue`, `drifted`,
  `missing`), optional `note`. The `Literal` types make any other value a 422.

**Response models**:

* `WhitelistEntry` — a persisted exemption (`id`, `target_type`, `target_identifier`, `reason`,
  `created_at`).
* `AuditLogEntry` — one `audit_log` row (`id`, `action`, `target`, `timestamp`, `details`,
  `hostname`).
* `ActionResult` — what every operator action returns: `ok`, `action`, `target`, `message`, and
  `pr_url` for adoptions. The front end shows `message` in a toast and renders `pr_url` as a link.
* `SettingView` — one editable setting on Cluster Config: `key` (settings attribute),
  `env` (variable name), `label`, `kind` (`mode`, `int`, `float`, `text`; picks the input
  widget), `value` (effective), `default` (environment value), `overridden`, `help`.
* `BatchResult` — `operation`, `scope`, one `ActionResult` per target, and `pr_url` for a
  consolidated Adopt All PR. `succeeded` / `failed` are counting properties; the batch route adds
  them to the JSON.

---

## `__init__.py`

Re-exports the names other packages are expected to use, so `from shared import DriftStatus,
HostSnapshot` works. Not everything is re-exported: `COMPOSE_CONTAINER_NUMBER_LABEL`,
`COMPOSE_ONEOFF_LABEL`, `COMPOSE_LABEL_PREFIX`, `WARDEN_ACTION_LABEL`,
`DEFAULT_EXCLUDED_NAME_PREFIXES` and the less common exceptions (`ConfigurationError`,
`BaselineParseError`, `ContainerRecreateError`, `AdoptionError`) are imported from their
module directly (`from shared.constants import ...`, `from shared.exceptions import ...`), which
is what the engine does.

---

## How to extend the contract

* **New drift status**: add it to `DriftStatus`, then to `STATUS_META` and `STATUS_ORDER` in
  `web/routes/views.py` (label, colour, sort rank) and to the rules in
  `warden/engine/diff_engine.py`. The dashboard counters and `HostSnapshot.counts()` pick it up
  automatically.
* **New audit action**: add the enum member and call `store.record_audit(...)` where it happens.
  Nothing else needs to change; the Events Log renders any value.
* **New compared field**: add it to `DIFF_FIELDS`, handle it in `diff_engine.compare`, in
  `observed_service`, in `baseline_parser.normalise_service` / `service_to_mapping`, and in
  `yaml_mutator.update_service` if adoption should sync it.
* **New operator action**: add its name to the `actions` list the diff engine produces, a
  reconciler coroutine, a route in `web/routes/api_actions.py`, an entry in the `ACTIONS` table
  of `dashboard.js`, and the button in `dashboard.html` / `diff.html` / `rowHtml`.
* **New field on `DriftRecord`**: because `extra="forbid"` is set, every place that constructs a
  record must be updated (`_rogue_record` and `_managed_record` in the diff engine, plus the
  tests). Give it a default if it is optional.
