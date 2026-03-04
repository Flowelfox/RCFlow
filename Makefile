.PHONY: help install dev lint format typecheck test coverage check run migrate-gen migrate migrate-down bundle bundle-linux bundle-windows start-emulator setup-emulator flutter-run flutter-build flutter-release flutter-windows clean
.DEFAULT_GOAL := help

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{ printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2 }'

install: ## Install production dependencies
	uv sync

dev: ## Install with dev dependencies
	uv sync --extra dev
	pre-commit install

lint: ## Linting
	uv run ruff check src/ tests/

format: ## Format and fix code
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

typecheck: ## Type checking
	ty check src/

test: ## Run tests
	uv run pytest tests/ -v

coverage: ## Run tests with coverage
	uv run pytest tests/ -v --cov=src --cov-report=term-missing

check: lint typecheck test ## Run all checks (lint + typecheck + test)

run: ## Run the server
	uv run rcflow

migrate-gen: ## Generate a new Alembic migration (msg="..")
	uv run alembic revision --autogenerate -m "$(msg)"

migrate: ## Apply migrations
	uv run alembic upgrade head

migrate-down: ## Rollback last migration
	uv run alembic downgrade -1

bundle: ## Build distributable package for current platform
	uv run python scripts/bundle.py

bundle-linux: ## Build Linux distributable (must be on Linux)
	uv run python scripts/bundle.py --platform linux

bundle-windows: ## Build Windows distributable (must be on Windows)
	uv run python scripts/bundle.py --platform windows

start-emulator: ## Start Windows Android emulator (cold boot)
	./scripts/start-emulator.sh

setup-emulator: ## Setup WSL2 ADB connection to Windows emulator
	./scripts/setup-emulator.sh

flutter-run: ## Run Flutter app in hot reload mode
	cd rcflowclient && flutter run -d $(shell grep nameserver /etc/resolv.conf | awk '{print $$2}'):15555

flutter-build: ## Build Flutter debug APK
	cd rcflowclient && flutter build apk --debug
	@mkdir -p build/artifacts
	cp rcflowclient/build/app/outputs/flutter-apk/app-debug.apk build/artifacts/

flutter-release: ## Build Flutter release APK (split per ABI)
	cd rcflowclient && flutter build apk --release --split-per-abi
	@mkdir -p build/artifacts
	cp rcflowclient/build/app/outputs/flutter-apk/app-*.apk build/artifacts/

flutter-windows: ## Build Flutter Windows desktop app (release)
	cd rcflowclient && flutter build windows --release
	@mkdir -p build/artifacts
	cp -r rcflowclient/build/windows/x64/runner/Release build/artifacts/windows

clean: ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf dist/ build/ *.egg-info/ htmlcov/ .coverage
