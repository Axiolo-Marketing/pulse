.PHONY: dev down test backend-shell migrate makemigration build deploy-check deploy-apply

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

# ── Frontend build (runs in the frontend container; output → ./dist) ──────

build:
	docker compose exec frontend npm run build

# ── Production deploy via Ansible ─────────────────────────────────────────
# Both targets prompt for the ansible-vault password. `deploy-check` is the
# safety net — always read every diff before running `deploy-apply` against
# the shared VPS. See deploy/README.md for the full runbook.

deploy-check: build
	cd deploy && ansible-playbook deploy.yml --ask-vault-pass --check --diff

deploy-apply: build
	cd deploy && ansible-playbook deploy.yml --ask-vault-pass
