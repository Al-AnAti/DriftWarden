# DriftWarden

DriftWarden is a lightweight, local GitOps guardrail and drift-remediation daemon for standalone Docker hosts. It ensures that running infrastructure perfectly matches a declarative Git baseline (`docker-compose.yml`), neutralizing configuration drift and unauthorized "shadow IT" workloads through real-time Docker socket monitoring.

## 🎯 Project Purpose

Standalone Docker hosts often suffer from configuration drift when engineers bypass CI/CD pipelines to manually execute `docker run` commands or edit live containers for emergency hotfixes. DriftWarden solves this by providing a closed-loop GitOps reconciliation engine directly on the host machine. 

**Core Capabilities:**
*   **Zero-Trust Host Monitoring:** Detects rogue containers lacking managed Compose namespace tags.
*   **Real-time Diff Engine:** Compares live host state against a remote GitHub declarative baseline.
*   **Remediation Paths:** Resolves conflicts through two distinct mechanisms: 
    *   **Enforce (Revert):** Terminates and recreates drifted containers to strictly match the Git baseline.
    *   **Adopt (Reverse-GitOps):** Reverse-engineers manual container hotfixes into YAML AST mutations to generate automated Pull Requests.
## 🤝 Project Ownership Boundary

This capstone is collaboratively developed with strict domain boundaries to minimize merge conflicts and ensure architectural separation of concerns:

*   **Noor Al-deen:** Core Daemon Engine. Responsible for the `docker-py` event stream ingestion, GitHub REST API (`PyGithub`) integration, YAML AST parsing (`ruamel.yaml`), Zero-Trust boundary logic, and CLI toolchain (`Makefile`).
*   **Naeam:** Control Plane & Persistence. Responsible for the FastAPI web server, SQLite database schema (`SQLAlchemy`), internal API routing, and the Jinja2/Tailwind CSS interactive dashboard.

## ⚠️ Security Warning: Docker Socket Access

**CRITICAL:** DriftWarden requires a direct bind to the host's Docker socket (`/var/run/docker.sock`) to subscribe to the event stream and issue remediation commands. Access to the Docker socket is equivalent to root-level execution privileges on the host machine. 
*   This tool is intended for standalone development and staging environments.
*   Do not expose the FastAPI Control Plane port (default: 8000) to the public internet without an external reverse proxy and authentication layer.

## 🚀 Local Installation & Environment Setup

DriftWarden requires Python 3.10+ and a local Docker engine.

**1. Clone the repository & create an isolated virtual environment:**
```bash
git clone <your-repo-url>
cd DriftWarden
python -m venv venv
source venv/bin/activate  # On Windows PowerShell: .\venv\Scripts\activate
```

**2. Install dependencies in development mode:**
```bash
make install-dev
```

**3. Configure Environment Variables:**
Copy the example template and populate it with your GitHub Personal Access Token (PAT) and repository details.
```bash
cp .env.example .env
```
*Note: Ensure your GitHub PAT has the `repo` scope to read YAML files and create Pull Requests.*

## ⚙️ Operating Modes

DriftWarden is configured via the `WARDEN_MODE` environment variable to support different deployment safety requirements:

*   **Observe Mode (`WARDEN_MODE=observe`):** The engine monitors the Docker socket, detects drift, flags rogue containers, and logs alerts to SQLite, but **never automatically mutates the host**. Remediation requires a human to click [Revert] or [Adopt] in the web UI. *(Recommended for Production)*
*   **Enforce Mode (`WARDEN_MODE=enforce`):** The engine acts ruthlessly and autonomously. It immediately terminates rogue containers and kills/recreates drifted containers to match the Git baseline without waiting for human approval.

## 🛠️ Development Workflow & Make Targets

DriftWarden relies on a `Makefile` to abstract complex shell commands and ensure consistent development practices.

**Core Workflow:**
*   `make start` - Boots the FastAPI Control Plane and the async Engine loop with live-reload enabled.
*   `make check` - Runs formatting, linting, strict type-checking (`mypy`), and the test suite without mutating files. Run this before every Git commit.
*   `make clean` - Wipes all Python caches (`__pycache__`) and deletes the local SQLite database to start fresh.

**Code Quality Targets:**
*   `make format` - Formats the codebase using Ruff.
*   `make lint` - Runs Ruff static analysis to catch syntax or import errors.
*   `make typecheck` - Validates strict type boundaries using MyPy.
*   `make test` - Executes the full `pytest` suite.

**Capstone Presentation Demo Runners:**
*   `make demo-infra-up` - Bootstraps the baseline test infrastructure from local fixtures.
*   `make demo-drift` - Injects targeted chaos into the host (spawns an unlabelled rogue database, kills a required web container, and mutates a cache container's image tag while spoofing namespace labels).
*   `make demo-clean` - Wipes all demo containers and resets the local Docker daemon.