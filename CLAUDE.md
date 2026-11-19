# DriftWarden

GitOps drift guardrail for a standalone Docker host. A `docker-compose.yml` in a GitHub repo is
the source of truth. **Enforce** recreates live containers to match it; **Adopt** opens a PR that
writes the live state back into Git. Full docs: `README.md` and `docs/` (overview, shared, warden, web).

## Stack
- Python >= 3.10 (write 3.10 syntax; the local venv may be newer). Packaged with setuptools from `src/`.
- FastAPI + uvicorn, Jinja2 server-rendered templates, vanilla JS (`dashboard.js`), Tailwind via CDN, SSE for live snapshots.
- docker-py (`docker` SDK) for Docker, PyGithub for GitHub, ruamel.yaml (round-trip, keeps comments).
- pydantic v2 + pydantic-settings for models and config; SQLAlchemy 2.0 (`DeclarativeBase`, `Mapped`) on SQLite. No SQLModel.
- loguru for logging. pytest + pytest-asyncio (`asyncio_mode = "auto"`), ruff, mypy, poethepoet.

## Commands
All tasks live in `[tool.poe.tasks]` in `pyproject.toml` and run as `warden <task>` (there is no
Makefile). Install first with `pip install -e ".[dev]"`. Run `warden` alone to list tasks.
- `warden check`: CI gate (ruff format --check, ruff check, mypy, pytest). Must stay green.
- `warden test [-k expr]`, `warden lint`, `warden typecheck`, `warden format`, `warden coverage`.
- `warden dev [-p PORT]`: uvicorn with reload on the host (uses `DATABASE_URL` from `.env`).
- `warden start | stop | logs`: run as a container via `scripts/container.sh` (DB at `./data/warden.db`).
- `warden demo-up | demo-drift | demo-down`, `warden reset-logs`, `warden clean`.
- Direct test run: `PYTHONPATH=src python -m pytest tests -q`.

## Layout
- `src/shared/`: pydantic models, enums/constants, exceptions. Imports nothing from `warden` or `web`.
- `src/warden/`: the engine. Imports `shared`, never `web`.
  - `config.py`: `WardenSettings` (pydantic-settings).
  - `core/`: `baseline_parser`, `docker_watcher` (all Docker calls), `host_identity`.
  - `engine/`: `diff_engine`, `state_projector`, `reconciler`, `store.py` (`StateStore` Protocol).
  - `automation/`: `github_client` (PyGithub), `yaml_mutator` (ruamel edits for Adopt PRs).
- `src/web/`: FastAPI app (`app.py`, `runtime.py`), `routes/` (`views`, `api_actions`),
  `db/` (`models_db`, `repository` implementing `StateStore`, `session` with additive migrations),
  `templates/`, `static/`.
- `tests/`: pytest suite; `fakes.py` (FakeWatcher, FakeGitHub), `fixtures/docker-compose.test.yaml`.
- `scripts/`: shell helpers behind the tasks, `reset_logs.py`. `docs/`: design docs.
- `images/`: the hand-finished presentation slides (`0-*.svg` to `5-*.svg`) and `logo.png`;
  `images/src/` holds the old generator scripts.

## Strict conventions
- Import root is `src` (`PYTHONPATH=src`): import `shared`, `warden`, `web`, never `src.shared`.
- mypy `strict = true` must pass on `src`, `tests` and `scripts` (`warden typecheck` covers only `src`,
  so run `mypy src tests scripts` too). ruff line length 100, rules E, F, I.
- Commit straight to local `main` with atomic conventional commits (`feat:`, `fix:`, `docs:`,
  `build:`, `test:`). No feature branches or PRs for this repo, and never push.
- Never edit `.env`; override with environment variables. The app never writes it either
  (UI overrides go to the `setting_overrides` table).
- Docker access only through docker-py in `docker_watcher.py`; Enforce uses `containers.create` and
  `start`, never the `docker compose` CLI or `subprocess`. Git only through PyGithub, no local clone.
- Adopt PRs never commit env values (keys become `${KEY}` placeholders) and never write `container_name`.
- Audit log rows are immutable; reversals are new rows linked by `reverses_id`. The JSON `snapshot`
  column is never exposed through the API or UI.
- Schema changes are additive and go in `Database._migrate` in `src/web/db/session.py`.
- Never run `images/src/build.py`: the numbered SVG slides are hand-edited and the generators
  would not reproduce them. `images/logo.png` is original artwork.
- In Jinja, put `| tojson` values inside single-quoted HTML attributes.
- Never stop or remove containers you did not create (the host runs an unrelated `ollama` container).
