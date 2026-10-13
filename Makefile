SHELL := /bin/bash
PYTHON ?= .venv/bin/python
PIP    ?= .venv/bin/pip
RUFF   ?= .venv/bin/ruff

.PHONY: help install run test lint docker-up docker-logs docker-down update-ytdlp clean

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Create venv and install runtime + dev dependencies
	python3.12 -m venv .venv
	$(PIP) install --upgrade pip setuptools
	$(PIP) install -r requirements-dev.txt

run: ## Run the bot (long polling)
	$(PYTHON) -m bot.main

test: ## Run the test suite
	$(PYTHON) -m pytest

lint: ## Lint with ruff
	$(RUFF) check bot tests

docker-up: ## Build and start via docker compose
	docker compose up -d --build

docker-logs: ## Tail docker compose logs
	docker compose logs -f --tail=100

docker-down: ## Stop docker compose stack
	docker compose down

update-ytdlp: ## Self-update yt-dlp inside the local venv
	$(PIP) install -U yt-dlp

clean: ## Remove venv, caches and runtime data
	rm -rf .venv .pytest_cache .ruff_cache