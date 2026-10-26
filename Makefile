# ------------------------------------------
# Cross-Platform Bash Routing
# ------------------------------------------

ifeq ($(OS),Windows_NT)
	# Force Windows to use Git Bash instead of intercepting via WSL
	BASH_CMD := "C:\Program Files\Git\bin\bash.exe"
else
	BASH_CMD := bash
endif

# ==========================================
# DriftWarden Makefile
# ==========================================

export PYTHONPATH = src

.PHONY: install install-dev format lint typecheck test coverage start dev stop logs check clean reset-logs demo-up demo-drift demo-down

IMAGE ?= driftwarden:latest
CONTAINER ?= driftwarden
PORT ?= 8000

# ------------------------------------------
# Setup & Dependency Management
# ------------------------------------------

install:
	@echo "📦 Installing core DriftWarden dependencies..."
	pip install -e .

install-dev:
	@echo "🛠️ Installing development tools and dependencies..."
	pip install -e ".[dev]"

# ------------------------------------------
# Code Quality & Testing
# ------------------------------------------

format:
	@echo "🎨 Formatting codebase with Ruff..."
	ruff format .
	ruff check --fix .

lint:
	@echo "🔍 Linting codebase with Ruff..."
	ruff check .

typecheck:
	@echo "ʦ Running static type analysis with MyPy..."
	mypy src/

test:
	@echo "🧪 Running all unit tests..."
	pytest tests/ -v

coverage:
	@echo "📊 Running unit tests with coverage..."
	pytest tests/ -q --cov=src --cov-report=term-missing

check:
	@echo "🚦 Running CI pipeline checks (No files will be mutated)..."
	ruff format --check .
	ruff check .
	mypy src/
	pytest tests/

# ------------------------------------------
# Execution & Cleanup
# ------------------------------------------

# Containerised run (make start / stop / logs) goes through scripts/container.sh
# so the recipe works from cmd.exe, PowerShell and Git Bash alike: it builds the
# image, mounts the Docker socket and keeps the SQLite database in ./data.
start:
	$(BASH_CMD) scripts/container.sh start $(IMAGE) $(CONTAINER) $(PORT)

stop:
	$(BASH_CMD) scripts/container.sh stop $(IMAGE) $(CONTAINER) $(PORT)

logs:
	$(BASH_CMD) scripts/container.sh logs $(IMAGE) $(CONTAINER) $(PORT)

# Host-based run with hot reload for development (database: DATABASE_URL in .env).
dev:
	@echo "🚀 Starting DriftWarden on the host with live reload..."
	uvicorn web.app:app --app-dir src --reload --port $(PORT)

clean:
	$(BASH_CMD) scripts/clean.sh

reset-logs:
	python scripts/reset_logs.py

# ------------------------------------------
# Final Presentation Demo Runners
# ------------------------------------------

demo-up:
	$(BASH_CMD) scripts/demo.sh up

demo-drift:
	$(BASH_CMD) scripts/demo.sh drift

demo-down:
	$(BASH_CMD) scripts/demo.sh clean
