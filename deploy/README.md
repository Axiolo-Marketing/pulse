# Pulse deploy (Ansible, shared Debian VPS)

This playbook deploys Pulse onto a Debian VPS that **already hosts other
applications**. Every role is scoped to Pulse's own paths, systemd units,
and database; nothing globally shared is touched. The `preflight` role
asserts the box already has Postgres, nginx, certbot, and a recent
Python — it never installs system packages.

## Resources Pulse owns (and nothing else)

| Resource | Path / name |
|---|---|
| App code | `/opt/pulse/api/` (Python venv inside) |
| Static frontend | `/var/www/pulse/` |
| Uploads | `/var/lib/pulse/uploads/` |
| Backend env | `/etc/pulse/pulse.env` (mode 0640) |
| Systemd unit | `pulse-api.service` |
| Backup cron | `/etc/cron.d/pulse-backup` |
| Backups | `/var/backups/pulse/` |
| DB user | `pulse` system user |
| Database | `pulse` (inside the system Postgres) |
| DB roles | `pulse_owner`, `pulse_anon`, `pulse_admin` |
| nginx site | `/etc/nginx/sites-available/pulse` + symlink |
| TLS cert | `/etc/letsencrypt/live/<domain>/` |

Everything else is **off-limits** — the playbook does not modify
`postgresql.conf`, `pg_hba.conf` (beyond the auth lines for the pulse
roles, which `community.postgresql` adds), `nginx.conf`, `conf.d/`, other
vhosts, certbot's renewal timer, system Python, ssh config, firewall
rules, or any other application's files.

## One-time bootstrap (by hand on the VPS)

The playbook can't bootstrap things that would require global config
changes. Do these once, as root or with `sudo`:

```bash
# 1. Install Python 3.13 if not already present (use the host's package
#    manager of choice — backports, deadsnakes, pyenv, whatever fits).
python3.13 --version

# 2. Make sure Postgres, nginx, certbot, rsync are installed.
psql --version && nginx -v && certbot --version && rsync --version

# 3. If your Postgres uses md5 auth, add a pg_hba line for the pulse
#    roles via the Unix socket so the playbook can connect:
#    local  pulse  pulse_owner,pulse_anon,pulse_admin  md5
#    (and reload Postgres: systemctl reload postgresql)

# 4. Create a 'deploy' user with passwordless sudo for the commands the
#    playbook runs (or just run Ansible as root over SSH — your call).
```

## Configuring

```bash
cd deploy/

# Install the Postgres collection that postgres-pulse uses.
ansible-galaxy collection install community.postgresql ansible.posix

# Edit non-secrets:
$EDITOR group_vars/all.yml           # domain, paths, port, etc.

# Copy + populate the vault:
cp vault.yml.example vault.yml
$EDITOR vault.yml                    # paste DB passwords, OAuth secrets, etc.
ansible-vault encrypt vault.yml      # encrypts in place

# Set your host:
$EDITOR inventory.yml                # ansible_host, ansible_user
```

OAuth client setup (one-time, in the provider consoles):

- **Google Cloud Console** → OAuth 2.0 Client IDs → Web application →
  authorized redirect URI = `https://<your-domain>/api/auth/google/callback`.
- **Azure AD** → App registrations → Web platform → redirect URI = same
  shape. API permissions: `openid`, `email`, `profile`.

Paste the resulting client ids + secrets into `vault.yml`.

## Build + deploy

```bash
# Build the Astro static site locally — the playbook rsyncs from `../dist/`.
cd ..
npm run build
cd deploy/

# DRY RUN — read every diff. Anything outside Pulse's owned paths is a bug.
ansible-playbook deploy.yml --ask-vault-pass --check --diff

# APPLY for real.
ansible-playbook deploy.yml --ask-vault-pass
```

## After the first successful deploy

1. The initial admin user (`pulse_initial_admin_email` from `group_vars/all.yml`)
   has been seeded as `is_admin = true` with no password set. Visit
   `https://<domain>/admin/`, click **Forgot password?**, enter the admin
   email, then click the link in the reset email. Or use **Continue with
   Google / Microsoft** if the admin email is on a tenant that matches
   one of the OAuth providers — the backend will link the OAuth identity
   to the pre-seeded user.

2. Verify the four health checks:
   - `https://<domain>/` returns the Pulse landing page.
   - `https://<domain>/api/healthz` returns `{"status":"ok"}`.
   - `systemctl status pulse-api` is active + running.
   - `journalctl -u pulse-api -n 50` shows uvicorn startup, no errors.

3. Smoke-test other apps on the box are still up — open their URLs,
   check their service status. If anything regressed, `ansible-playbook
   deploy.yml --check --diff` will help you see what was applied.

## ClickUp integration setup

The ClickUp integration is optional — Pulse works without it; the
"Connect ClickUp" + "Push to ClickUp" buttons just stay hidden until
the operator wires it up. To enable it:

1. **Create the OAuth app** at <https://app.clickup.com/settings/team/apps>.
   Set the redirect URI to `https://<pulse-domain>/api/auth/clickup/callback`.
   ClickUp gives you a client id and a client secret.

2. **Generate a Fernet encryption key**:
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   This key encrypts every secret Pulse stores in Postgres (ClickUp
   tokens, ClickUp webhook secrets). **Losing it means every encrypted
   column becomes permanently unrecoverable** — back it up wherever you
   keep `vault_pulse_session_secret`.

3. **Paste both** into `deploy/vault.yml`:
   ```yaml
   vault_pulse_clickup_client_id:     "..."
   vault_pulse_clickup_client_secret: "..."
   vault_pulse_encryption_keys:       "<fernet-key>"   # comma-separated for rotation
   ```
   Re-encrypt the vault and re-run the playbook so `/etc/pulse/pulse.env`
   picks up the new values, then `systemctl restart pulse-api`.

4. **In Pulse admin**, click **Connect ClickUp** in the top-right. You'll
   be sent to ClickUp's consent screen; on return, Pulse stores the
   OAuth access token (encrypted) and registers a webhook on each
   workspace you have access to.

5. **For each engagement**, click into the detail view, then **Set
   ClickUp list** — paste the URL of the ClickUp list that should
   receive the engagement's cards (e.g.
   `https://app.clickup.com/12345/v/li/901234567`). Pulse extracts the
   numeric id and looks up the list name for display.

6. **Click Push to ClickUp**. Pulse pushes one task per card (idempotent
   — re-pushes update rather than duplicate). File-upload cards push
   their files as ClickUp attachments. The summary toast shows
   `N created / M updated / K attachments / errors`.

The push uses Pulse's 8 status names — your ClickUp list must have
matching statuses for the API to accept them (see
`src/lib/status-suggest.ts` for the canonical list). If a status doesn't
match, the per-card error from ClickUp is collected and surfaced in the
push summary; other cards still push.

### Key rotation

To rotate the Fernet encryption key:
1. Generate a new key.
2. Prepend it to `vault_pulse_encryption_keys` (comma-separated, new key
   first). Re-deploy. The app now encrypts new writes with the new key,
   but still decrypts old ciphertext using the old key.
3. Run a one-off data migration (TODO: not built for v1) that
   re-encrypts every `*_enc` column using the current primary key.
4. Drop the old key from the list. Re-deploy.

For v1, just hold the key permanent and back it up offsite.

## Subsequent deploys

```bash
git pull
npm run build
ansible-playbook deploy/deploy.yml --ask-vault-pass
```

The `backend` role syncs source, runs `uv sync --frozen`, runs
`alembic upgrade head`, and restarts `pulse-api`. The `frontend` role
rsyncs `dist/`. nginx isn't reloaded unless the site config changed.

## Backups

Daily, via `/etc/cron.d/pulse-backup`:
- `pg_dump pulse | gzip` → `/var/backups/pulse/db-YYYY-MM-DD.sql.gz`
- `rsync -a --delete /var/lib/pulse/uploads/ /var/backups/pulse/uploads/`
- 30-day local retention.

Off-box copies are out of scope for the playbook. Recommended: set up
`rclone` (or aws-cli) on the host with credentials for an R2/B2 bucket,
add a Pulse-only cron that runs after the local backup cron.

## What this playbook does NOT do

Intentionally. These would all be destructive on a shared box:

- Install or update system packages
- Modify `postgresql.conf`, `nginx.conf`, the certbot renewal timer
- Configure firewalls, ssh, or system-wide log rotation
- Touch other vhosts, other systemd units, other databases
- Anything in `/root`, `/home/*`, or other users' files
