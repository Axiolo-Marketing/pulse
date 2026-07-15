.PHONY: dev down test back-lint backend-shell migrate makemigration build deploy-check deploy-apply seed-dev seed-deck reset-test-db front-lint front-typecheck front-test front-check test-e2e install-hooks

# ── Local dev ──────────────────────────────────────────────────────────────

dev:
	docker compose up --build

down:
	docker compose down

backend-shell:
	docker compose exec backend bash

# ── Tests + migrations ─────────────────────────────────────────────────────

test:
	docker compose exec -T backend uv run pytest

# Ruff lint over the backend package. Config lives in api/pyproject.toml
# ([tool.ruff]); wired-but-passing on the current code (see M6 in the audit
# — this is not a repo-wide reformat gate).
back-lint:
	docker compose exec -T backend uv run ruff check pulse_api

migrate:
	docker compose exec -T backend uv run alembic upgrade head

makemigration:
	@if [ -z "$(m)" ]; then echo "usage: make makemigration m='describe the change'"; exit 1; fi
	docker compose exec -T backend uv run alembic revision --autogenerate -m "$(m)"

# ── Dev helpers ────────────────────────────────────────────────────────────
# Idempotently seed a verified admin user (dev@example.com / dev-admin-password
# by default; override with DEV_ADMIN_EMAIL=... etc). Use this before doing
# manual UI work in /admin/ instead of hand-running SQL.
seed-dev:
	docker compose exec -T backend uv run python -m scripts.dev_seed

# Idempotently seed a full client-facing demo deck (a client → engagement →
# recipient → cards covering every response type) under the dev Axiolo org.
# Prints a stable recipient token + deck URL you can open in a browser. Run
# `make seed-dev` first (creates the org). Safe to re-run — reuses the same
# engagement/recipient via the fixed token (override with DEV_DECK_TOKEN=...).
seed-deck:
	docker compose exec -T backend uv run python -m scripts.dev_seed_deck

# Drop + recreate the pulse_test database. Useful when switching branches has
# left it at a migration revision that doesn't exist on the current branch.
# The test fixtures will re-run alembic upgrade head on the next `make test`.
reset-test-db:
	docker compose exec -T backend uv run python -m scripts.reset_test_db

# ── Frontend build (runs in the frontend container; output → ./dist) ──────

build:
	docker compose exec -T frontend npm run build

# ── Frontend checks — the LOCAL "CI" (we don't run CI; see deploy/README) ──
# Run in the frontend container. `front-check` is the full gate (lint → unit
# tests → build) and is what .githooks/pre-push enforces on every push. `build`
# is `astro check && astro build`, so the typecheck happens inside it — no
# separate typecheck step needed in the gate. Individual targets are here for
# convenience.

front-lint:
	docker compose exec -T frontend npm run lint

front-typecheck:
	docker compose exec -T frontend npm run typecheck

front-test:
	docker compose exec -T frontend npm test

front-check:
	docker compose exec -T frontend sh -c "npm run lint && npm test && npm run build"

# Playwright E2E — runs on the HOST, not in a container: Playwright's browsers
# need glibc, which the alpine dev container lacks. Installs host deps + chromium
# on first run. Requires the dev stack up (`make dev`) + seeded deck
# (`make seed-deck`). global-setup resets the demo recipient each run.
test-e2e:
	npm ci --no-audit --no-fund && npx playwright install chromium && npx playwright test

# One-time per clone: point git at the tracked hooks dir so .githooks/pre-push
# runs `front-check` before every push. Bypass once with `git push --no-verify`.
install-hooks:
	git config core.hooksPath .githooks
	@echo "Installed: .githooks/pre-push will run 'make front-check' on push."

# ── Production deploy via Ansible ─────────────────────────────────────────
# The Astro + Python build runs on the VPS (git pull → uv sync → npm build),
# so there is no local build step here. The vault password is read from
# deploy/vault_secret (gitignored); `--ask-become-pass` prompts once for the
# VPS sudo password. `deploy-check` is the safety net — always read every diff
# before running `deploy-apply` against the shared VPS. See deploy/README.md.

deploy-check:
	cd deploy && ansible-playbook deploy.yml --vault-password-file vault_secret --ask-become-pass --check --diff

deploy-apply:
	cd deploy && ansible-playbook deploy.yml --vault-password-file vault_secret --ask-become-pass
