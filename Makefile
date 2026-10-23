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

.PHONY: install install-dev format lint typecheck test coverage start dev stop check clean reset-logs demo-up demo-drift demo-down

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

# Containerised run: builds the image, mounts the Docker socket and keeps the
# SQLite database in ./data so it survives container recreation.
start:
	@echo "🐳 Building and starting the DriftWarden container on http://localhost:$(PORT) ..."
	docker build -t $(IMAGE) .
	-docker rm -f $(CONTAINER) >/dev/null 2>&1
	mkdir -p data
	docker run -d --name $(CONTAINER) \
		-p $(PORT):8000 \
		--env-file .env \
		-e API_HOST=0.0.0.0 \
		-e API_PORT=8000 \
		-e DATABASE_URL=sqlite:////data/warden.db \
		-e DOCKER_SOCKET_PATH=/var/run/docker.sock \
		-v //var/run/docker.sock:/var/run/docker.sock \
		-v "$(CURDIR)/data:/data" \
		$(IMAGE)
	@echo "✅ Container $(CONTAINER) is up; database in ./data/warden.db. Stop with: make stop"

# Host-based run with hot reload for development.
dev:
	@echo "🚀 Starting DriftWarden on the host with live reload..."
	uvicorn web.app:app --app-dir src --reload --port $(PORT)

stop:
	-docker rm -f $(CONTAINER)

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
