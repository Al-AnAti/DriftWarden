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

.PHONY: install install-dev format lint typecheck test test-warden start check clean demo-infra-up demo-drift demo-clean

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

check:
	@echo "🚦 Running CI pipeline checks (No files will be mutated)..."
	ruff format --check .
	ruff check .
	mypy src/
	pytest tests/

# ------------------------------------------
# Execution & Cleanup
# ------------------------------------------

start:
	@echo "🚀 Starting DriftWarden Control Plane & Engine Loop..."
	uvicorn web.app:app --app-dir src --reload --port 8000

clean:
	$(BASH_CMD) scripts/clean.sh

# ------------------------------------------
# Final Presentation Demo Runners
# ------------------------------------------

demo-infra-up:
	$(BASH_CMD) scripts/demo.sh infra-up

demo-drift:
	$(BASH_CMD) scripts/demo.sh drift

demo-clean:
	$(BASH_CMD) scripts/demo.sh clean