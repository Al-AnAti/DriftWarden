# DriftWarden Agent Instructions & Architecture Guidelines

You are an expert autonomous software engineer working on **DriftWarden**, a lightweight GitOps guardrail and drift-remediation daemon for standalone Docker hosts. You write production-grade, cleanly typed Python 3.10+ code that strictly adheres to the architectural boundaries defined below.

---

## 1. System Architecture & Flow

DriftWarden runs directly on the host machine to eliminate configuration drift and shadow IT:
1. **Baseline Ingestion:** Fetches the declarative source of truth (`docker-compose.yml`) via stateless GitHub REST API calls (`PyGithub`).
2. **Runtime Event Stream:** Watches the host Docker daemon in real-time via `/var/run/docker.sock` (`docker-py`).
3. **Reconciliation & Diffing:** Compares live containers against the Git baseline.
4. **Remediation Paths:**
   - **Enforce:** Surgically terminates rogue/unauthorized containers via Docker SDK.
   - **Adopt (Reverse-GitOps):** Inspects live container state, mutates the Compose AST using `ruamel.yaml` (preserving comments and formatting), and submits an automated Pull Request via PyGithub.
5. **Lifespan Integration:** The watcher, reconciler, and state projector run entirely inside FastAPI's `@asynccontextmanager` application lifespan event loop on the same process.
6. **State Buffer (SQLite):** During an Adopt action, the engine writes the container to a local `pending_adoptions` SQLite table to suppress false-positive drift alerts while waiting for the GitHub Actions CI/CD pipeline to deploy the merged code.
---

## 2. Directory & Scope Boundaries

Adhere to the repository layout:

.
├── .env
├── .env.example
├── Makefile
├── scripts/
│   ├── trigger_drift.sh
│   └── trigger_rogue.sh
├── src/
│   ├── shared/
│   │   ├── constants.py
│   │   ├── exceptions.py
│   │   └── models.py
│   ├── warden/
│   │   ├── config.py
│   │   ├── automation/
│   │   │   ├── github_client.py
│   │   │   └── yaml_mutator.py
│   │   ├── core/
│   │   │   ├── baseline_parser.py
│   │   │   └── docker_watcher.py
│   │   └── engine/
│   │       ├── diff_engine.py
│   │       ├── reconciler.py
│   │       └── state_projector.py
│   └── web/
│       ├── app.py
│       ├── db/
│       ├── routes/
│       ├── static/
│       └── templates/
└── tests/

### Protected Files
- **NEVER** edit or overwrite `.env`. Assume all necessary environment secrets exist locally.
- Use `.env.example` as the reference for available environment variable names.
---

## 3. Strict Coding Standards

### Modern Python & Type Annotations
- Target **Python 3.10+**.
- Always use union operators (`X | None` or `int | str`) instead of `typing.Optional` or `typing.Union`.
- Strictly type-annotate all function signatures, arguments, and return types.

### Data Modeling: Strict Boundary Separation
- **I/O & Boundary Data (Pydantic v2):** Any data crossing network, database, or API boundaries (FastAPI endpoints, GitHub payloads, SQLite queries, control plane messages) MUST use Pydantic models defined in `src/shared/models.py`.
- **Internal High-Throughput State (`@dataclass`):** Internal engine processing (such as raw Docker socket events or `ruamel.yaml` AST tokens) MUST use standard library `@dataclass` definitions to eliminate unnecessary validation overhead.
- **No Raw Dictionaries:** Never pass untyped `dict[str, Any]` across module boundaries between `warden/` and `web/`.

### Logging & Error Handling
- **Loguru Only:** Use `from loguru import logger` for all logging. Do not use the standard `logging` module or raw `print()` statements.
- **Custom Exceptions:** All custom exceptions must inherit from `DriftWardenError` in `src/shared/exceptions.py`. Use descriptive child exceptions (e.g., `GitBaselineFetchError`, `RogueContainerKillError`, `YAMLMutationError`).

### Documentation
- Use **Google-style docstrings** on all public classes, methods, and functions.
- Explicitly document input arguments, return values, expected side effects, and raised exceptions.

---

## 4. Strictly Prohibited Patterns ("Do Not Use")

1. **NO Subprocess Git Commands or GitPython:** Never run `subprocess.run(['git', ...])` or import `git`. All Git operations must be stateless GitHub REST API calls via `PyGithub`.
2. **NO SPA Frameworks or Node.js Ecosystem:** Do not introduce `npm`, `package.json`, React, Vue, or build-step bundlers. The frontend is strictly Jinja2 server-side templates with Tailwind CSS loaded via CDN and vanilla JavaScript.
3. **NO Inter-Process Communication (IPC):** Do not spawn the engine as a separate OS process, Celery worker, or background daemon. The engine tasks must run cooperatively inside FastAPI's async event loop.
4. **NO Raw YAML Parsers:** Never use `pyyaml` (`import yaml`). Use `ruamel.yaml` with round-trip loading (`typ="rt"`) to preserve comments, indentation, and structure during Reverse-GitOps AST mutations.

---

## 5. Verification Commands (Makefile Protocol)

Always validate modifications using the project `Makefile`. Execute these exact commands before considering any task complete:

- **Format Code:** `make format` (runs `ruff format .` and `ruff check --fix .`)
- **Lint Code:** `make lint` (runs `ruff check .`)
- **Run Test Suite:** `make test` (runs `pytest tests/ -v`)
- **Local Server Verification:** `make start` (runs `uvicorn src.web.app:app --reload --port 8000`)

### Test Environment Assumptions
- Docker engine is active locally with the socket at `/var/run/docker.sock`.
- When writing tests, mock external Docker daemon events and GitHub REST API calls using `pytest-mock` or `unittest.mock`. Do not execute live GitHub mutations or kill active host containers during unit test runs.

---

## 6. Commit Style

Write commit messages following the Conventional Commits specification:
- `feat: <description>` for new capabilities (e.g., new engine filters, adoption handlers).
- `fix: <description>` for bug corrections and edge-case drift handling.
- `refactor: <description>` for internal code reorganizations without behavior changes.
- `test: <description>` for test suite additions and fixtures.
- `docs: <description>` for documentation and docstring updates.