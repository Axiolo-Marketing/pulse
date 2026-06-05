# Multi-tenant SaaS migration — execution workflow

Companion to `~/.claude/plans/let-s-plan-on-makding-mossy-orbit.md`. That file is the *what*; this file is the *how* — how the main session (orchestrator) drives the 6 PRs through sub-agents.

## Why this workflow exists

The plan is large (3–4 weeks of work, 6 PRs, every layer touched). Running it inline from the main session burns context and risks the orchestrator losing the big picture by PR 4. Delegating each phase to a fresh sub-agent keeps the orchestrator's context focused on coordination, integration, and gating.

## Agent roster

| Agent | When invoked | Purpose |
|---|---|---|
| **Orchestrator** (main session) | Throughout | Sequencing, integration, gating, committing |
| **Architect** (`feature-dev:code-architect`) | Start of each PR | Translates the relevant plan section into a concrete blueprint (files, signatures, sequence). Only used when the PR touches unfamiliar code or has multiple viable approaches. |
| **Backend coder** (`general-purpose`) | Implementation | Writes Python/SQL/Alembic. Must invoke `python-dev:python-standards` skill before writing code. |
| **Frontend coder** (`general-purpose`) | Implementation (UI PRs) | Writes Astro/TS/CSS. Must invoke `frontend-dev:frontend-standards` and consult `frontend-design:frontend-design` skill knowledge before writing components. |
| **UI/UX supervisor** (`general-purpose`) | Before *and* after every frontend change | (a) Pre-build: reviews mockups/component sketches from the frontend coder against modern UX/accessibility/layout/brand standards using `frontend-design:frontend-design` knowledge. (b) Post-build: audits the diff against the same rubric and the rendered UI via Playwright MCP screenshots. |
| **Test author** (`general-purpose`) | After implementation | Writes pytest (parametrized-first via `python-dev:python-testing` skill) + Vitest + Playwright. E2E coverage is non-negotiable; main-session gates on it. |
| **Test runner** (`test-runner`) | After test author | Runs `make test`, returns failure analysis (does not fix). |
| **Code reviewer** (`feature-dev:code-reviewer`) | After tests pass | Independent review for bugs, security, conventions, simplification opportunities. |
| **Git workflow** (`git-workflow`) | End of PR | Branch hygiene, conventional commits, PR creation. |

## Per-PR loop (the orchestrator runs this 6 times)

```
┌─ 0. Setup ─────────────────────────────────────────────────┐
│  Orchestrator creates branch: feature/mt-pr<N>-<slug>      │
└────────────────────────────────────────────────────────────┘
            │
┌─ 1. Architect (conditional) ───────────────────────────────┐
│  Skip for trivial PRs. For PRs touching new surface area,  │
│  spawn feature-dev:code-architect with the relevant plan   │
│  section + file paths. Output: concrete blueprint.         │
└────────────────────────────────────────────────────────────┘
            │
┌─ 2a. UI/UX pre-design (frontend PRs only) ─────────────────┐
│  Spawn UI/UX supervisor with the user flow + target pages. │
│  Output: ASCII layout, component list, state diagram, copy │
│  decks. Orchestrator reviews + asks user only if a real    │
│  product decision surfaces (auto-mode default: proceed).   │
└────────────────────────────────────────────────────────────┘
            │
┌─ 2b. Implementation ───────────────────────────────────────┐
│  Spawn Backend coder and/or Frontend coder in parallel     │
│  when the file sets don't overlap. Each agent receives:    │
│   - the plan section verbatim                              │
│   - exact files to create/edit                             │
│   - architectural constraints (RLS, role-flip, etc.)       │
│   - "invoke <skill> first" reminder                        │
└────────────────────────────────────────────────────────────┘
            │
┌─ 3. UI/UX post-build review (frontend PRs only) ───────────┐
│  Spawn UI/UX supervisor again with the diff. Uses          │
│  Playwright MCP to navigate the dev server, capture        │
│  screenshots at mobile + desktop breakpoints, and audit:   │
│    • semantic HTML / a11y (keyboard, ARIA, contrast)       │
│    • mobile-first responsive behavior                      │
│    • Axiolo brand tokens used (no hardcoded colors)        │
│    • loading/empty/error states present                    │
│    • motion + microinteractions where appropriate          │
│    • copy clarity                                          │
│  Returns blocker list. Orchestrator loops back to (2b) if  │
│  any blockers; otherwise advances.                         │
└────────────────────────────────────────────────────────────┘
            │
┌─ 4. Tests ─────────────────────────────────────────────────┐
│  Spawn Test author with the implementation diff and the    │
│  PR's E2E inventory (below). Output:                       │
│    • Pytest: parametrized unit + endpoint tests; new RLS   │
│      isolation cases mirror tests/test_rls_isolation.py    │
│    • Vitest: any new pure TS modules                       │
│    • Playwright (via MCP): every E2E scenario for this PR  │
│  Coverage must stay ≥ 80% (pyproject gate).                │
└────────────────────────────────────────────────────────────┘
            │
┌─ 5. Test runner ───────────────────────────────────────────┐
│  Spawn test-runner. Runs `make test` + Playwright suite.   │
│  If fails: orchestrator hands failure back to Backend or   │
│  Frontend coder for fix; re-run.                           │
└────────────────────────────────────────────────────────────┘
            │
┌─ 6. Code review ───────────────────────────────────────────┐
│  Spawn feature-dev:code-reviewer with the full diff.       │
│  Orchestrator triages: must-fix → back to coder; nits →    │
│  fix or note for follow-up; security/correctness → fix.    │
└────────────────────────────────────────────────────────────┘
            │
┌─ 7. Final verification ────────────────────────────────────┐
│  Orchestrator runs the PR's verification checklist (from   │
│  the plan's Verification section) and exercises the        │
│  feature in the browser via /run.                          │
└────────────────────────────────────────────────────────────┘
            │
┌─ 8. Commit + PR ───────────────────────────────────────────┐
│  Spawn git-workflow agent with the PR description + plan   │
│  reference. Returns PR URL.                                │
└────────────────────────────────────────────────────────────┘
```

## Backend agent rules

Every Backend coder prompt opens with:

1. **Read `CLAUDE.md` first.** Especially the "role-flip pattern" and three test gotchas.
2. **Invoke `python-dev:python-standards` skill** before writing code (PEP 8, type hints, Google-style docstrings, Ruff, DRY, separation of concerns).
3. **RLS is the backstop.** New tenant tables get RLS policies + the `pulse_member` role + a `pulse.org_id` GUC check. Never write a route that "filters in Python only" against tenant data.
4. **Secrets at rest are Fernet-encrypted** (memory: `feedback_secrets_at_rest.md`).
5. **No raw SQL in repos** unless the existing module already does it — use SQLModel.
6. **Migrations**: raw SQL via `op.execute()` for RLS/triggers/grants; SQLModel for tables. Match the style of `0001_initial_schema.py`.

## Frontend agent rules

Every Frontend coder prompt opens with:

1. **Read `CLAUDE.md` first.** Especially the brand system section and the `src/lib/api.ts` invariant ("no module imports from anywhere else for HTTP").
2. **Invoke `frontend-dev:frontend-standards` skill** (TS, React, TailwindCSS, shadcn/ui, Framer Motion, oxlint, Prettier).
3. **Mobile-first.** Test mobile breakpoint first; desktop is the secondary view.
4. **Brand tokens.** Use CSS custom properties from `src/styles/pulse.css` (`--primary`, `--ink`, etc.). No hardcoded hex.
5. **No `window.alert/confirm/prompt`** (memory: `feedback_no_alerts.md`). Inline errors near trigger; `toast()` for transient notices.
6. **All HTTP through `src/lib/api.ts`.**

## UI/UX supervisor rubric

The UI/UX supervisor judges every frontend change against:

| Dimension | Pass criteria |
|---|---|
| Accessibility | Keyboard-navigable, focus rings visible, ARIA labels on icon-only buttons, color contrast ≥ 4.5:1, prefers-reduced-motion honored |
| Mobile-first | Layout works at 360px wide; no horizontal scroll; tap targets ≥ 44×44px |
| Responsive | Layout adapts at 768px and 1280px breakpoints; no broken truncation |
| Brand | Uses CSS variables (no hex literals in new code); Plus Jakarta Sans; spacing rhythm matches existing pages |
| States | Loading, empty, error, success all designed and implemented |
| Motion | Microinteractions on state changes (Framer Motion if React); no jank |
| Copy | Action verbs on buttons; clear empty-state CTAs; no jargon |
| Information density | Progressive disclosure for advanced settings; primary action above the fold |

Pre-build deliverable from this agent: ASCII layout mock + state machine + copy deck.
Post-build deliverable: Playwright MCP screenshots at 360/768/1280 + blocker/nit list.

## Test author rules

Every Test author prompt opens with:

1. **Invoke `python-dev:python-testing` skill first.** Parametrization is the default; one test function per scenario is the exception.
2. **Mirror existing patterns.** Endpoint tests use `httpx.AsyncClient` + `ASGITransport(app)` + transaction-rollback fixture. RLS isolation tests follow `tests/test_rls_isolation.py`.
3. **E2E via Playwright MCP** for every user-facing scenario in the PR's inventory. Test on mobile viewport too.
4. **No mocking the DB.** Integration tests hit the test DB (feedback memory).
5. **Coverage gate is 80%.** New code lifts the average; if your PR drops it, add more tests.

### Parametrization checklist (test author must apply)

- Cross-org isolation: parametrize over `(actor_role, resource_org, expected_status)` — replaces ~6 hand-written cases with one parametrized function.
- Role enforcement: parametrize over `(role, route, method, expected_status)` for owner-vs-member gates.
- Invite acceptance variants: parametrize over `(auth_method, has_existing_user, expected_outcome)` — covers password/Google/Microsoft × new/existing user.
- Token expiry: parametrize over `(token_age, expected)` for invites and sessions.
- Audit log writes: parametrize over `(action, target_type, expected_metadata_keys)`.

## E2E test inventory (per PR)

These are the Playwright MCP scenarios the test author must implement. The orchestrator gates the PR on all of them passing in the dev environment.

### PR 1 — schema + migration
- No UI E2E (data-layer PR). E2E covered by `tests/test_multi_tenant_isolation.py` running pytest-only.

### PR 2 — auth/session refactor
- Sign in as the seeded admin → session payload contains `active_org_id` matching the Axiolo org → `/api/me` returns the same.
- API key created before PR 2 still works after migration (org_id backfilled).
- OAuth callback with an unknown email and no invite → lands on a "you need an invitation" page.

### PR 3 — org routes + invite backend
- (Pytest only — no UI yet.) But the inventory is the contract the UI in PR 4 will exercise.

### PR 4 — admin UI
- **Org switcher.** Multi-org owner signs in, sees switcher; switches; admin chrome updates to new org's name/logo; client list reflects the new org.
- **Settings split.** Personal tab shows API keys; Organization tab shows name/logo/members/invites (owner) or name/logo/members read-only (member).
- **Logo upload.** Owner uploads logo, sees preview, saves; client-facing deck for any of the org's clients shows the new logo header.
- **Invite flow.** Owner invites `someone@example.com` as member; captured email contains a link; opening link in incognito shows the org name + role; setting a password completes acceptance; lands in the admin with member-scoped UI.
- **Member management.** Owner promotes a member to owner; demotes back; removes a member; UI updates without reload.
- **Mobile.** All flows above at 360px viewport.

### PR 5 — superadmin
- Superadmin sees `/admin/#superadmin` in nav; non-superadmin does not.
- Superadmin creates "Acme" org with `jane@acme.example` as owner; org appears in the list; captured email contains the invite link.
- Acceptance flow same as PR 4 but the new owner is in a brand-new org with zero clients.

### PR 6 — audit log + activity UI
- Every action from PRs 4–5 emits an audit entry; activity feed shows them in reverse-chronological order.
- Filter by user + action narrows correctly.
- Cross-org isolation: an Axiolo activity entry is invisible from the Acme activity feed (RLS test in pytest).

## What the orchestrator does NOT delegate

- Reading the user's intent and updating the plan when scope shifts.
- Deciding between two architectural alternatives the architect surfaced.
- Approving any destructive action (force push, migration rollback, dropping prod tables).
- The final "is this done" call before opening the PR.
- Writing this file or the plan file (those are decisions, not artifacts).

## Kickoff command

When ready to start a PR, the orchestrator says one of:

- `start PR 1` → schema + migration + RLS
- `start PR 2` → auth/session refactor
- `start PR 3` → org routes + invite backend
- `start PR 4` → admin UI
- `start PR 5` → superadmin
- `start PR 6` → audit log + activity UI

The orchestrator then runs the per-PR loop above, with the relevant section of `~/.claude/plans/let-s-plan-on-makding-mossy-orbit.md` as the source of truth for *what* and this file as the source of truth for *how*.
