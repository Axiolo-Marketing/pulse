#!/usr/bin/env node
// Apply schema.sql and seed.sql to Supabase Postgres via direct connection.
// Reads SUPABASE_DB_URL from env. Run via:
//   node --env-file=.env.local scripts/apply-sql.mjs

import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import pg from "pg";

const here = dirname(fileURLToPath(import.meta.url));
const sqlDir = resolve(here, "..", "supabase");

const dbUrl = process.env.SUPABASE_DB_URL;
if (!dbUrl) {
  console.error(
    "Missing SUPABASE_DB_URL. Pass it inline:\n" +
      "  SUPABASE_DB_URL='postgres://...' node scripts/apply-sql.mjs"
  );
  process.exit(1);
}

const files = ["schema.sql", "seed.sql"];

const client = new pg.Client({
  connectionString: dbUrl,
  ssl: { rejectUnauthorized: false },
});

await client.connect();

try {
  for (const name of files) {
    const path = resolve(sqlDir, name);
    const sql = await readFile(path, "utf8");
    process.stdout.write(`Applying ${name}... `);
    await client.query(sql);
    console.log("ok");
  }
  console.log("\nAll SQL applied.");
} finally {
  await client.end();
}
