.PHONY: dev down test backend-shell migrate makemigration build deploy-check deploy-apply seed-dev reset-test-db

# ── Local dev ──────────────────────────────────────────────────────────────

dev:
	docker compose up --build

down:
	docker compose down

backend-shell:
	docker compose exec backend bash

# ── Tests + migrations ─────────────────────────────────────────────────────

test:
	docker compose exec backend uv run pytest

migrate:
	docker compose exec backend uv run alembic upgrade head

makemigration:
	@if [ -z "$(m)" ]; then echo "usage: make makemigration m='describe the change'"; exit 1; fi
	docker compose exec backend uv run alembic revision --autogenerate -m "$(m)"

# ── Dev helpers ────────────────────────────────────────────────────────────
# Idempotently seed a verified admin user (dev@example.com / dev-admin-password
# by default; override with DEV_ADMIN_EMAIL=... etc). Use this before doing
# manual UI work in /admin/ instead of hand-running SQL.
seed-dev:
	docker compose exec backend uv run python -m scripts.dev_seed

# Drop + recreate the pulse_test database. Useful when switching branches has
# left it at a migration revision that doesn't exist on the current branch.
# The test fixtures will re-run alembic upgrade head on the next `make test`.
reset-test-db:
	docker compose exec backend uv run python -m scripts.reset_test_db

# ── Frontend build (runs in the frontend container; output → ./dist) ──────

build:
	docker compose exec frontend npm run build

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
