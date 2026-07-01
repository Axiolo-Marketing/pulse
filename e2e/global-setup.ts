import { execSync } from "node:child_process";

const TOKEN = "dec0ded0dec0ded0";

// Resets the demo recipient (seeded by `make seed-deck`, token above) so the
// deck spec always starts fresh at card 1. Runs the DELETEs through the dev
// `db` container via stdin (avoids quoting the SQL on the command line).
// Best-effort: if docker/the stack isn't up, the deck spec will just resume
// wherever it left off. Skip entirely with PULSE_E2E_NO_RESET=1.
export default function globalSetup(): void {
  if (process.env.PULSE_E2E_NO_RESET === "1") return;
  const sql = `DELETE FROM responses WHERE recipient_id = (SELECT id FROM recipients WHERE token='${TOKEN}');
DELETE FROM uploads WHERE recipient_id = (SELECT id FROM recipients WHERE token='${TOKEN}');`;
  try {
    execSync(
      `docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`,
      { input: sql, stdio: ["pipe", "ignore", "inherit"] },
    );
  } catch {
    console.warn(
      "[e2e] could not reset the demo recipient (is the db container up?); the deck spec may resume mid-deck.",
    );
  }
}
