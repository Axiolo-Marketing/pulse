# Pulse deploy (Ansible, shared Debian VPS)

This playbook deploys Pulse onto a Debian VPS that **already hosts other
applications**. Every role is scoped to Pulse's own paths, systemd units,
and database. The `preflight` role installs missing apt prerequisites with
`state: present` only, starts/enables Postgres and nginx, and then leaves
shared configuration alone.

## Resources Pulse owns (and nothing else)

| Resource | Path / name |
|---|---|
| App source | `/opt/pulse/source/` (git checkout — backend at `api/`, Astro at `src/`) |
| Python venv | `/opt/pulse/venv/` (outside source so `git clean` cannot wipe it) |
| Static frontend | `/var/www/pulse` (symlink → `/opt/pulse/source/dist/`) |
| Uploads | `/var/lib/pulse/uploads/` |
| Backend env | `/etc/pulse/pulse.env` (mode 0640) |
| Deploy key | `/etc/pulse/deploy_key` (mode 0600 — read-only SSH key for the repo) |
| Backup DB auth | `/etc/pulse/pgpass` (mode 0600) |
| Systemd unit | `pulse-api.service` |
| Backup cron | `/etc/cron.d/pulse-backup` |
| Backups | `/var/backups/pulse/` |
| DB user | `pulse` system user |
| Database | `pulse` (inside the system Postgres) |
| DB roles | `pulse_owner`, `pulse_anon`, `pulse_member`, `pulse_admin` |
| nginx site | `/etc/nginx/sites-available/pulse` + symlink |
| TLS cert | `/etc/letsencrypt/live/<domain>/` |

Everything else is **off-limits** — the playbook does not upgrade system
packages, add apt repositories, modify `postgresql.conf`, `pg_hba.conf`,
`nginx.conf`, `conf.d/`, other vhosts, certbot's renewal timer, ssh config,
firewall rules, or any other application's files.

## One-time bootstrap

The playbook installs normal Debian apt packages if they are missing. Do
only the pieces that require credentials, DNS, or intentional global config:

```bash
# 1. Point DNS before the first TLS run.
#    pulse.axiolo.com -> 198.27.127.130

# 2. If Python 3.13 is not available from the host's configured apt repos,
#    install it deliberately before running the playbook. The playbook will
#    not add third-party apt repositories by itself.
python3.13 --version

# 3. Ensure the SSH user in inventory can sudo. For the shared VPS this is
#    currently ansible_user=gabriel at 198.27.127.130.

# 4. The playbook uses the same SSH key as the other Axiolo deploys
#    (image-compressor, sitechecker, octoping) — `~/.ssh/github_deploy_key`
#    on the operator's machine. It has access to all Axiolo-Marketing
#    repos, so no per-repo Deploy Key is needed. Confirm it exists:
ls -l ~/.ssh/github_deploy_key
#    If a different path is preferred, override pulse_deploy_key_path in
#    group_vars/all.yml.
```

## Configuring

```bash
cd deploy/

# Install the Postgres collection that postgres-pulse uses.
ansible-galaxy collection install community.postgresql ansible.posix

# Edit non-secrets:
$EDITOR group_vars/all.yml           # domain, paths, port, etc.

# Secrets live as inline ansible-vault strings at the bottom of
# group_vars/all.yml. To set or rotate one:
ansible-vault encrypt_string --vault-password-file vault_secret 'VALUE' --name vault_pulse_resend_api_key
# ...then paste the !vault block over the existing one in group_vars/all.yml.

# Set your host:
$EDITOR inventory.yml                # ansible_host, ansible_user
```

OAuth client setup (one-time, in the provider consoles):

- **Google Cloud Console** → OAuth 2.0 Client IDs → Web application →
  authorized redirect URI = `https://<your-domain>/api/auth/google/callback`.
- **Azure AD** → App registrations → Web platform → redirect URI = same
  shape. API permissions: `openid`, `email`, `profile`.

Encrypt the resulting client ids + secrets with `ansible-vault encrypt_string`
(as above) and paste each `!vault` block into `group_vars/all.yml`.

### Outbound email (Resend)

Transactional mail (org invites, email verification, password reset) is sent
via [Resend](https://resend.com)'s HTTPS API. Setup:

1. **Sending domain.** `pulse_email_from` defaults to
   `Pulse <pulse@notifications.axiolo.com>`, reusing the already-verified
   `notifications.axiolo.com` domain — Resend authorizes any mailbox on a
   verified domain, so no new DNS is needed. To send from a `pulse.axiolo.com`
   address instead, first add `pulse.axiolo.com` as a **separate** Resend domain
   and publish the SPF/DKIM records it shows you (subject to your plan's domain
   limit). For a quick local test before any domain is verified, send from
   Resend's sandbox sender `onboarding@resend.dev` to your own Resend-account
   email.
2. **Vault the API key.** Create an API key in Resend, then:

   ```bash
   ansible-vault encrypt_string --vault-password-file vault_secret 're_...' --name vault_pulse_resend_api_key
   ```

   Paste the `!vault` block over `vault_pulse_resend_api_key: ""` in
   `group_vars/all.yml`, and set `pulse_email_from` to an address on the
   verified domain.

Until the key is set, the app stays in **log-only** mode — `send_email` logs
the message (recipient, subject, link) and sends nothing. Failures never raise:
the link is always recoverable from `journalctl -u pulse-api`, so a Resend
outage can't roll back org/invite creation.

### Reactive cards (Anthropic API)

The reactive-cards feature (SPEC §15: LLM-generated follow-up questions when
a respondent corrects a confirm-edit card) is **default-off at three levels**
(env, per-org, per-engagement), so deploying the code changes nothing until
you opt in. The playbook templates the env vars from `group_vars/all.yml`
(`vault_pulse_anthropic_api_key`, `pulse_reactive_cards_enabled`,
`pulse_reactive_model`). Production enablement:

1. **Vault the API key** (same pattern as Resend):

   ```bash
   ansible-vault encrypt_string --vault-password-file vault_secret 'sk-ant-...' --name vault_pulse_anthropic_api_key
   ```

   Paste the `!vault` block over the `vault_pulse_anthropic_api_key: ""`
   placeholder in `group_vars/all.yml`, flip
   `pulse_reactive_cards_enabled: true`, and deploy. `pulse_reactive_model`
   selects the Claude model (default `claude-opus-4-8`; any id priced in
   `api/pulse_api/reactive.py`'s `MODEL_PRICING` keeps the superadmin cost
   estimates populated). `REACTIVE_FAKE_MODE` is deliberately not templated —
   it is a dev-only stub that fabricates follow-up cards without calling the
   API and must never be enabled in production.

2. **Allow an org** (superadmin): toggle on `/admin/#superadmin`, or
   `PATCH /api/superadmin/orgs/{id}` with `{"reactive_cards_allowed": true}`.
3. **Enable per engagement**: the operator flips "Reactive cards" in the
   edit-engagement dialog.

Cost visibility: every generation records tokens + `cost_usd` on
`card_generations`; the superadmin "Reactive cards usage" panel aggregates
per org (≈ $0.01–0.015 per generation on the default `claude-opus-4-8`).
Spend is bounded per respondent (attempt cap) and per correction (dedup +
2-card cap) — see SPEC §15.6.

### Multi-tenant config (v3.0)

The v2→v3 migration introduced four env vars that **must be set in
`/etc/pulse/pulse.env` before `alembic upgrade head` runs for the first
time**. The playbook templates them from `group_vars/all.yml`:

| Env var | Maps to | Default | Notes |
|---|---|---|---|
| `SUPERADMIN_EMAILS` | `pulse_admin_emails` (kept the v2 name — semantics shifted) | empty | Whitespace/comma-separated. Migration 0004 reads this at execution time and stamps `is_superadmin = true` on matching `users` rows. In v2 the same list seeded `is_admin = true` (now-removed column); in v3 it grants the cross-tenant superadmin tier. **If you forget to set this and run the migration anyway, no superadmin gets promoted** — recover with a one-shot `UPDATE users SET is_superadmin = true WHERE lower(email) = ...`. |
| `MEMBER_DATABASE_URL` | `pulse_member_database_url` | falls back to `DATABASE_URL` | Connection string for the `pulse_member` Postgres role (no `BYPASSRLS`). The backend opens `pulse_member` sessions for every operator request — RLS scopes to the active org. |
| `SIGNUP_ENABLED` | `pulse_signup_enabled` | `false` | Pulse is invite-only by design. The legacy `POST /api/auth/signup` endpoint returns 404 unless this is explicitly `true`. **Leave it false in production.** |
| `ENVIRONMENT` | `pulse_environment` | `production` | Anything other than `development` flips the `Secure` flag on session + state cookies. |

The four Postgres roles (`pulse_owner`, `pulse_anon`, `pulse_member`,
`pulse_admin`) are created idempotently by the `postgres-pulse` role on each
run. Migration `0004_multi_tenant.py` also creates `pulse_member` inside a
`DO $$` block so a fresh DB volume bootstrap is safe in either order.

## Build + deploy

The playbook clones the repo on the VPS and builds the Astro frontend
there. **Commit and push first** — Ansible deploys whatever is on
`{{ pulse_repo_branch }}` at origin, not your working tree.

```bash
git push    # make sure main is up-to-date

# DRY RUN — read every diff. Anything outside Pulse's owned paths is a bug.
ansible-playbook deploy.yml --ask-vault-pass --check --diff

# APPLY for real.
ansible-playbook deploy.yml --ask-vault-pass
```

## After the first successful deploy

1. **Superadmin first-login.** Every email in `pulse_admin_emails`
   (from `group_vars/all.yml`) was promoted to `is_superadmin = true` by the
   data migration in `0004_multi_tenant.py`. The migration also created the
   default **Axiolo organization** and added every superadmin as an owner of
   it. Each superadmin visits `https://<domain>/admin/` and clicks **Continue
   with Google / Microsoft**, signing in with their seeded address — the
   OAuth callback links their identity to the pre-seeded user. "Forgot
   password?" is NOT a valid first-login path: the reset flow skips users
   whose `password_hash IS NULL`. To grant superadmin to someone new, append
   to `pulse_admin_emails`, re-run the playbook, then run the one-shot
   `UPDATE users SET is_superadmin = true WHERE lower(email) = ':e'` against
   the prod DB (the env var is only read by `0004`'s data migration; it's
   not re-applied on subsequent boots).

2. **Verify the four health checks:**
   - `https://<domain>/` returns the Pulse landing page.
   - `https://<domain>/api/healthz` returns `{"status":"ok"}`.
   - `systemctl status pulse-api` is active + running.
   - `journalctl -u pulse-api -n 50` shows uvicorn startup, no errors.

3. **Multi-tenant smoke test.** Sign in as a superadmin, navigate to
   `https://<domain>/admin/#superadmin`, confirm the page renders and the
   Axiolo org appears in the list with `member_count >= 1` and the
   superadmin's email in `owner_emails`.

4. **Other apps on the shared box are still up.** Open their URLs, check
   their service status. If anything regressed,
   `ansible-playbook deploy.yml --check --diff` will help you see what was
   applied.

## Onboarding a new tenant org

In v3.0, every customer is an org. Pulse is **invite-only** — there is no
public signup, so each new org is created by a superadmin.

1. Sign in to `https://<domain>/admin/` as a superadmin.
2. Open `https://<domain>/admin/#superadmin`.
3. **Create organization** form:
   - Display name (e.g. `Acme Inc`)
   - Slug (lowercase, hyphenated, immutable — chosen carefully because it
     appears in audit logs)
   - Owner email
4. Submit. The backend atomically: creates the `organizations` row, creates
   a pending `organization_invites` row for the owner email with `role=owner`,
   signs a 7-day invite token, and emails the link
   (`https://<domain>/invite?token=…`).
5. The new owner clicks the link, picks **Continue with Google / Microsoft**
   or sets a password, and lands in their org's empty admin view. They can
   then invite their teammates from `#settings/organization`.

If the invite email goes missing (greylisting, typo'd address), the
superadmin can resend by deleting the pending invite (DELETE
`/api/orgs/me/invites/{id}` after a `switch-org` into the target org via the
header switcher) and re-issuing.

### Deleting an org

Only superadmins can delete orgs, and only when the org has **zero clients**
(safety guard — cascade would wipe customer data). The endpoint returns 409
with a message if the org still has engagements. Delete all clients first,
then `DELETE /api/superadmin/orgs/{org_id}` via the UI button.

## Subsequent deploys

```bash
git push
cd deploy/
ansible-playbook deploy.yml --ask-vault-pass
```

The `backend` role `git pull`s on the VPS, runs `uv sync --frozen`, runs
`alembic upgrade head`, and restarts `pulse-api`. The `frontend` role runs
`npm ci && npm run build` against the same checkout — nginx serves the
build output directly via the `/var/www/pulse` → `/opt/pulse/source/dist`
symlink that `preflight` creates, so there is no second copy of `dist/` on
disk. nginx is deployed once in HTTP bootstrap mode if the cert does not
exist, certbot obtains the cert via webroot, then nginx is deployed again
with HTTPS enabled.

**Migration safety.** Immediately before `alembic upgrade head`, the `backend`
role takes a fresh `pg_dump` → `/var/backups/pulse/db-predeploy-<ts>.sql.gz`
(the "Snapshot the Pulse DB before migrating" task). `pipefail` means a dump
that fails aborts the play *before* any migration runs — you never migrate a
DB you couldn't back up — and the snapshot is same-minute, not up to a day
stale like the 03:15 cron dump. It's skipped under `--check` (shell tasks
don't run in check mode), so the dry run stays read-only. Each `alembic
upgrade head` is then wrapped in a Postgres transaction (Alembic's default),
so a failing migration rolls back cleanly and `pulse-api` is restarted against
the previous schema. The 0004 data migration is the only one that reads env
vars at execution time — keep `SUPERADMIN_EMAILS` in `/etc/pulse/pulse.env`
from then on.

To roll a deploy back, restore the snapshot it took:
`gunzip -c /var/backups/pulse/db-predeploy-<ts>.sql.gz | psql pulse` (stop
`pulse-api` first, then redeploy the prior git ref).

## Backups

Daily, via `/etc/cron.d/pulse-backup`:
- `pg_dump pulse | gzip` → `/var/backups/pulse/db-YYYY-MM-DD.sql.gz`
- `rsync -a --delete /var/lib/pulse/uploads/ /var/backups/pulse/uploads/`
- 30-day local retention.

Per deploy, via the `backend` role (see Migration safety above):
- `pg_dump pulse | gzip` → `/var/backups/pulse/db-predeploy-<ts>.sql.gz`, taken
  right before `alembic upgrade head`; same 30-day local retention.

### Off-box backups (Cloudflare R2 via restic)

The local backups above all live on the same VPS disk — a lost/corrupted
disk or a bad `rm -rf` takes them out along with the app. The `pulse-backup`
cron optionally pushes the *same* local backup directory (`/var/backups/pulse/`
— DB dumps + the uploads mirror) off-box to Cloudflare R2 (an S3-compatible
object store) via [restic](https://restic.net), a 04:00 job right after the
03:15/03:30 local jobs.

**Status: off by default, and UNTESTED until an operator provisions R2 and
deploys it.** `pulse_restic_enabled: false` in `group_vars/all.yml` is the
master switch — do not flip it to `true` without following the bootstrap
below and verifying a manual `restic backup` first.

**Bootstrap (one-time, manual — the playbook does not do this for you):**

1. Create an R2 bucket + an API token scoped to it in the Cloudflare
   dashboard (R2 → Manage API tokens). Note the bucket name, the
   account-scoped S3 endpoint (`https://<accountid>.r2.cloudflarestorage.com`),
   the access key id, and the secret access key.
2. Fill in the non-secret vars in `group_vars/all.yml`:
   `pulse_restic_r2_bucket`, `pulse_restic_r2_endpoint`.
3. Vault the secrets (a strong restic repo password + the R2 credentials —
   see the comment above `vault_pulse_restic_password` in
   `group_vars/all.yml` for the exact `ansible-vault encrypt_string`
   invocations). **Never invent or commit a real secret value in plaintext.**
4. `restic` is installed by the `preflight` role's apt task (Debian's
   package, `state: present`). SSH to the VPS as the `pulse` user (or
   `sudo -u pulse`) and initialize the repo **once**, by hand:
   ```bash
   export RESTIC_REPOSITORY="s3:https://<accountid>.r2.cloudflarestorage.com/<bucket>"
   export RESTIC_PASSWORD="<the restic repo password you vaulted>"
   export AWS_ACCESS_KEY_ID="<R2 access key id>"
   export AWS_SECRET_ACCESS_KEY="<R2 secret access key>"
   restic init
   restic backup /var/backups/pulse --tag pulse-daily --host pulse   # verify it works
   restic snapshots
   ```
5. Only after step 4 succeeds, set `pulse_restic_enabled: true` and re-run
   the playbook — this templates the 04:00 restic job into
   `/etc/cron.d/pulse-backup` (see `pulse-backup.cron.j2`). It backs up
   `/var/backups/pulse` (tag `pulse-daily`), then runs
   `restic forget --keep-daily {{ pulse_restic_retention_days }} --prune`
   to enforce retention on the R2 side independently of the 30-day local
   retention.

**Restore procedure:**

- **Fast path (local, same box, most rollbacks — e.g. a bad deploy).** Stop
  `pulse-api`, then:
  ```bash
  gunzip -c /var/backups/pulse/db-predeploy-<ts>.sql.gz | psql pulse
  # or, for a routine daily dump instead of a pre-deploy snapshot:
  gunzip -c /var/backups/pulse/db-YYYY-MM-DD.sql.gz | psql pulse
  ```
  Uploads: the local mirror at `/var/backups/pulse/uploads/` can be
  `rsync`'d back to `/var/lib/pulse/uploads/` directly.
- **Disaster recovery (R2, when the VPS disk itself is gone/corrupted —
  requires a fresh host with `restic` installed).** Export the same four env
  vars as step 4 above, then:
  ```bash
  restic snapshots                          # find the snapshot id/date you want
  restic restore latest --target /var/backups/pulse-restored
  # DB: gunzip -c /var/backups/pulse-restored/db-YYYY-MM-DD.sql.gz | psql pulse
  # Uploads: rsync -a /var/backups/pulse-restored/uploads/ /var/lib/pulse/uploads/
  ```
  Restore to a scratch directory first (as above), not directly over a live
  `/var/lib/pulse/uploads/` — confirm the snapshot is the one you want before
  overwriting anything.

## What this playbook does NOT do

Intentionally. These would all be destructive on a shared box:

- Upgrade system packages or add package repositories
- Modify `postgresql.conf`, `nginx.conf`, the certbot renewal timer
- Configure firewalls, ssh, or system-wide log rotation
- Touch other vhosts, other systemd units, other databases
- Anything in `/root`, `/home/*`, or other users' files

## Known follow-ups

These are stale-but-non-breaking pieces left over from the v2→v3 migration.
None block deploys; all should land in a focused PR.

- **Rename `pulse_admin_emails` → `pulse_superadmin_emails`** in
  `group_vars/all.yml` and update the `postgres-pulse` role's seed task. The
  current variable name still works (the env var it templates is
  `SUPERADMIN_EMAILS`), but the name is misleading in v3 — operators reading
  the YAML expect it to mean "per-org admins" when it actually means
  "cross-tenant superadmins."
- **Inline seed query in `postgres-pulse`**: if the role contains an SQL
  template that references `is_admin`, update it to use
  `users.is_superadmin` and to also insert an `organization_memberships`
  row joining the user to the Axiolo org as `owner`. The migration's data
  migration already does this once, but the Ansible-driven re-seed on
  every deploy needs to match the new model.
- **Per-tenant from-address** is not in scope yet — all org invite emails go
  out from `pulse_email_from` (the platform's `pulse@notifications.axiolo.com`,
  sent via Resend). The recipient sees the platform brand, not the inviting
  org's brand. Acceptable for v3; revisit if customers ask.
