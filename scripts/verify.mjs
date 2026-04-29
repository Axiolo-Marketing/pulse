#!/usr/bin/env node
// Milestone 1 confirmation script.
// Reads SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY from env (Node 25 --env-file
// loads .env.local), then verifies that schema + seed are in place.
//
// Run with: npm run verify:supabase

import { createClient } from "@supabase/supabase-js";

const url = process.env.SUPABASE_URL;
const serviceKey = process.env.SUPABASE_SERVICE_ROLE_KEY;

if (!url || !serviceKey) {
  console.error(
    "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY in environment.\n" +
      "Copy .env.example to .env.local and fill in real values, then re-run."
  );
  process.exit(1);
}

const supabase = createClient(url, serviceKey, {
  auth: { autoRefreshToken: false, persistSession: false },
});

const RENEE_ID = "00000000-0000-0000-0000-000000000001";

console.log("Verifying Pulse Supabase setup...\n");

// 1. Renee's client row
const { data: client, error: clientErr } = await supabase
  .from("clients")
  .select("id, name, org_name, engagement_name, token, created_at")
  .eq("id", RENEE_ID)
  .single();

if (clientErr || !client) {
  console.error("FAIL: client row not found.", clientErr?.message ?? "");
  process.exit(1);
}

console.log("Client:");
console.log(`  ${client.name} (${client.org_name})`);
console.log(`  engagement: ${client.engagement_name}`);
console.log(`  token:      ${client.token}`);
console.log(`  created at: ${client.created_at}\n`);

// 2. All 19 cards
const { data: cards, error: cardsErr } = await supabase
  .from("cards")
  .select("order_index, category, title, response_type, skip_allowed")
  .eq("client_id", RENEE_ID)
  .order("order_index", { ascending: true });

if (cardsErr) {
  console.error("FAIL: cards query failed.", cardsErr.message);
  process.exit(1);
}

console.log(`Cards: ${cards.length} found (expected 19)`);
for (const c of cards) {
  const skip = c.skip_allowed ? "skip ok" : "REQUIRED";
  console.log(
    `  ${String(c.order_index).padStart(2, " ")}. [${c.category}] ${c.title}  (${c.response_type}, ${skip})`
  );
}

if (cards.length !== 19) {
  console.error("\nFAIL: expected 19 cards.");
  process.exit(1);
}

// 3. Storage bucket
const { data: buckets, error: bucketErr } = await supabase.storage.listBuckets();
if (bucketErr) {
  console.error("FAIL: cannot list storage buckets.", bucketErr.message);
  process.exit(1);
}
const pulseBucket = buckets.find((b) => b.id === "pulse-uploads");
console.log(
  `\nStorage bucket pulse-uploads: ${pulseBucket ? "present" : "MISSING"}`
);
if (!pulseBucket) process.exit(1);

// 4. Renee's URL
const base = "https://tomdigati.github.io/pulse/";
console.log(`\nRenee's URL:\n  ${base}?t=${client.token}\n`);
console.log("Milestone 1 verified.");
