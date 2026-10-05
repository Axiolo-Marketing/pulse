import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { ActivityEntry } from "@/lib/api";
import { ACTION_LABELS, formatActivityPhrase } from "./ActivityFeed";

function entry(
  action: string,
  metadata: Record<string, unknown> | null,
): ActivityEntry {
  return {
    id: "1",
    created_at: "2026-01-01T00:00:00Z",
    actor: { user_id: "u", name: "A", email: "a@x.com" },
    action,
    target_type: null,
    target_id: null,
    metadata,
  } as ActivityEntry;
}

describe("ACTION_LABELS", () => {
  it("covers exactly the backend AUDIT_ACTIONS", () => {
    const src = readFileSync(
      resolve(process.cwd(), "api/pulse_api/audit.py"),
      "utf8",
    );
    const block = src.match(/AUDIT_ACTIONS[^=]*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/);
    expect(block).not.toBeNull();
    const actions = [...block![1].matchAll(/"([a-z_]+\.[a-z_]+)"/g)].map(
      (m) => m[1],
    );
    expect(actions.length).toBeGreaterThan(20);
    expect(Object.keys(ACTION_LABELS).sort()).toEqual([...actions].sort());
  });

  it("every action has a phrase distinct from the generic fallback", () => {
    for (const action of Object.keys(ACTION_LABELS)) {
      expect(formatActivityPhrase(entry(action, {}))).toBeTruthy();
    }
  });
});

describe("formatActivityPhrase", () => {
  const cases: [string, string, Record<string, unknown> | null, string][] = [
    ["engagement.create", "name", { name: "Acme" }, 'created engagement "Acme"'],
    ["engagement.update", "name", { name: "Acme" }, 'edited engagement "Acme"'],
    ["engagement.delete", "name", { name: "Acme" }, 'deleted engagement "Acme"'],
    ["engagement.delete", "null name", { name: null }, "deleted engagement (unknown)"],
    ["engagement.update", "null metadata", null, "edited engagement (unknown)"],
    ["card.create", "title", { title: "Q1" }, 'added card "Q1"'],
    ["card.update", "title", { title: "Q1" }, 'edited card "Q1"'],
    ["card.delete", "title", { title: "Q1" }, 'deleted card "Q1"'],
    ["attachment.upload", "file", { filename: "a.html" }, 'uploaded attachment "a.html"'],
    ["attachment.upload", "empty file", { filename: "" }, "uploaded attachment (unknown)"],
    ["upload.transcribe", "-", {}, "requested a voice transcript"],
    ["recipient.add", "email", { email: "r@x.com" }, "added respondent r@x.com"],
    ["recipient.remove", "email", { email: "r@x.com" }, "removed respondent r@x.com"],
    ["recipient.remove", "null email", { email: null }, "removed respondent (unknown)"],
    ["client.contact_save", "email", { email: "c@x.com" }, "saved client contact c@x.com"],
    ["client.contact_remove", "email", { email: "c@x.com" }, "removed client contact c@x.com"],
    ["org.create", "name", { name: "Org" }, 'created organization "Org"'],
    ["org.delete", "name", { name: "Org" }, 'deleted organization "Org"'],
    ["api_key.revoke", "label", { label: "ci" }, 'revoked API key "ci"'],
    ["api_key.create", "label+prefix", { label: "ci", prefix: "ab12" }, 'created API key "ci" (pulse_ab12…)'],
    ["api_key.create", "no prefix", { label: "ci" }, 'created API key "ci"'],
    ["member.invite", "email", { email: "m@x.com", role: "owner" }, "invited m@x.com (owner)"],
    ["member.remove", "no role", {}, "removed a member"],
    ["member.remove", "role", { former_role: "member" }, "removed a member (member)"],
    ["org.update", "rename", { old_name: "A", new_name: "B" }, 'renamed the organization from "A" to "B"'],
    ["org.update", "flag", { new_reactive_cards_allowed: true }, "enabled reactive cards for the organization"],
    ["engagement.reset", "counts", { responses_cleared: 1, uploads_cleared: 2 }, "reset engagement answers (1 response, 2 uploads cleared)"],
    ["engagement.invites_sent", "emails", { emails: ["a@x.com", "b@x.com"] }, "emailed the deck to a@x.com, b@x.com"],
    ["org.branding", "-", null, "updated the organization branding"],
  ];

  it.each(cases)("%s (%s)", (action, _label, meta, expected) => {
    expect(formatActivityPhrase(entry(action, meta))).toBe(expected);
  });

  it("never prints empty quotes or parentheses for null metadata", () => {
    for (const action of Object.keys(ACTION_LABELS)) {
      const phrase = formatActivityPhrase(entry(action, null));
      expect(phrase).not.toContain('""');
      expect(phrase).not.toContain("()");
    }
  });
});
