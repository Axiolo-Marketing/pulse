# Pulse deploy (Ansible, shared Debian VPS)

This playbook deploys Pulse onto a Debian VPS that **already hosts other
applications**. Every role is scoped to Pulse's own paths, systemd units,
and database. The `preflight` role installs missing apt prerequisites with
`state: present` only, starts/enables Postgres and nginx, and then leaves
shared configuration alone.

## Resources Pulse owns (and nothing else)

| Resource | Path / name |
|---|---|
| App code | `/opt/pulse/api/` (Python venv inside) |
| Static frontend | `/var/www/pulse/` |
| Uploads | `/var/lib/pulse/uploads/` |
| Backend env | `/etc/pulse/pulse.env` (mode 0640) |
| Backup DB auth | `/etc/pulse/pgpass` (mode 0600) |
| Systemd unit | `pulse-api.service` |
| Backup cron | `/etc/cron.d/pulse-backup` |
| Backups | `/var/backups/pulse/` |
| DB user | `pulse` system user |
| Database | `pulse` (inside the system Postgres) |
| DB roles | `pulse_owner`, `pulse_anon`, `pulse_admin` |
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
ansible-vault encrypt_string --vault-password-file vault_secret 'VALUE' --name vault_pulse_smtp_password
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

## Subsequent deploys

```bash
git pull
npm run build
cd deploy/
ansible-playbook deploy.yml --ask-vault-pass
```

The `backend` role syncs source, runs `uv sync --frozen`, runs
`alembic upgrade head`, and restarts `pulse-api`. The `frontend` role
rsyncs `dist/`. nginx is deployed once in HTTP bootstrap mode if the cert
does not exist, certbot obtains the cert via webroot, then nginx is deployed
again with HTTPS enabled.

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

- Upgrade system packages or add package repositories
- Modify `postgresql.conf`, `nginx.conf`, the certbot renewal timer
- Configure firewalls, ssh, or system-wide log rotation
- Touch other vhosts, other systemd units, other databases
- Anything in `/root`, `/home/*`, or other users' files
