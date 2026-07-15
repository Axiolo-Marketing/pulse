import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  orgsApi,
  type ActivityEntry,
  type MemberRow,
} from "@/lib/api";
import { formatTimestamp } from "@/lib/format-time";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

/** Stable dotted action enum → human-readable label. Kept in sync with
 * `AUDIT_ACTIONS` in `api/pulse_api/audit.py`. The keys double as the
 * "By action" filter's option values. */
const ACTION_LABELS: Record<string, string> = {
  "engagement.create": "Created engagement",
  "engagement.update": "Edited engagement",
  "engagement.delete": "Deleted engagement",
  "engagement.reset": "Reset engagement answers",
  "recipient.add": "Added respondent",
  "recipient.remove": "Removed respondent",
  "engagement.invites_sent": "Sent invites",
  "card.create": "Added card",
  "card.update": "Edited card",
  "card.delete": "Deleted card",
  "card.import": "Imported cards",
  "card.reactive_generate": "AI follow-up generated",
  "attachment.upload": "Uploaded attachment",
  "org.update": "Updated organization",
  "org.logo_set": "Updated logo",
  "org.logo_remove": "Removed logo",
  "org.branding": "Updated branding",
  "org.create": "Created organization",
  "org.delete": "Deleted organization",
  "member.invite": "Invited teammate",
  "member.invite_revoke": "Revoked invite",
  "member.role_change": "Changed member role",
  "member.remove": "Removed member",
  "member.join": "Joined organization",
  "api_key.create": "Created API key",
  "api_key.revoke": "Revoked API key",
};

// Radix's <SelectItem> rejects an empty-string value, so the "anyone"/"all"
// options carry sentinels that map back to "" in local state.
const ANY_ACTOR = "__any_actor__";
const ALL_ACTIONS = "__all_actions__";

const PAGE_SIZE = 50;

/** Read a metadata field as a string, treating null/undefined as empty. */
function str(v: unknown): string {
  return v == null ? "" : String(v);
}

/** Read a metadata field as a number, defaulting to 0. */
function num(v: unknown): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function actorName(entry: ActivityEntry): string {
  return entry.actor.name?.trim() || entry.actor.email || "Someone";
}

/** A lowercase-first phrase describing what happened, built from the action
 * plus its (defensively-read) metadata. Rendered as plain text after the
 * actor's name. */
export function formatActivityPhrase(entry: ActivityEntry): string {
  const m: Record<string, unknown> = entry.metadata ?? {};
  switch (entry.action) {
    case "engagement.create":
      return `created engagement "${str(m.name)}"`;
    case "engagement.reset":
      return `reset engagement answers (${num(m.responses_cleared)} responses, ${num(m.uploads_cleared)} uploads cleared)`;
    case "card.import":
      return `imported ${num(m.count)} card(s)`;
    case "card.reactive_generate": {
      const ids = Array.isArray(m.card_ids) ? m.card_ids : [];
      return `generated ${ids.length} AI follow-up card${ids.length === 1 ? "" : "s"}`;
    }
    case "org.update":
      if (m.old_name && m.new_name) {
        return `renamed the organization from "${str(m.old_name)}" to "${str(m.new_name)}"`;
      }
      if (typeof m.new_reactive_cards_allowed === "boolean") {
        return m.new_reactive_cards_allowed
          ? "enabled reactive cards for the organization"
          : "disabled reactive cards for the organization";
      }
      return "updated the organization";
    case "member.invite":
      return `invited ${str(m.email)} (${str(m.role)})`;
    case "member.role_change":
      return `changed a member's role from ${str(m.from)} to ${str(m.to)}`;
    case "member.remove":
      return `removed a member (${str(m.former_role)})`;
    case "member.join":
      return `joined the organization as ${str(m.role)}`;
    case "api_key.create":
      return `created API key "${str(m.label)}" (pulse_${str(m.prefix)}…)`;
    default:
      return (ACTION_LABELS[entry.action] ?? entry.action).toLowerCase();
  }
}

function memberLabel(m: MemberRow): string {
  return m.name?.trim() || m.email;
}

function ActivityRow({ entry }: { entry: ActivityEntry }): React.ReactElement {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <p className="min-w-0 text-sm text-foreground">
        <span className="font-medium">{actorName(entry)}</span>{" "}
        {formatActivityPhrase(entry)}
      </p>
      <span className="shrink-0 text-xs text-muted-foreground">
        {formatTimestamp(entry.created_at)}
      </span>
    </div>
  );
}

export function ActivityFeed(): React.ReactElement {
  const [actor, setActor] = useState("");
  const [action, setAction] = useState("");

  // The first page is owned by React Query and keyed by the filters, so
  // changing a filter refetches page 1 from scratch.
  const firstQuery = useQuery({
    queryKey: ["activity", actor, action],
    queryFn: () =>
      orgsApi.listActivity({
        limit: PAGE_SIZE,
        actor_user_id: actor || null,
        action: action || null,
      }),
  });

  const membersQuery = useQuery({
    queryKey: ["members"],
    queryFn: () => orgsApi.listMembers(),
  });

  // "Load more" pages are appended here; the cursor tracks the next fetch.
  // Both reset whenever a fresh first page arrives (i.e. the filters changed).
  const [morePages, setMorePages] = useState<ActivityEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  // Clear accumulated "load more" pages eagerly when a filter changes, so the
  // old pages never flash under the new filtered first page. The filter
  // handlers below call this; the effect only tracks the fresh page's cursor.
  function resetPages(): void {
    setMorePages([]);
    setCursor(null);
  }

  useEffect(() => {
    if (firstQuery.data) setCursor(firstQuery.data.next_cursor);
  }, [firstQuery.data]);

  async function loadMore(): Promise<void> {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await orgsApi.listActivity({
        limit: PAGE_SIZE,
        cursor,
        actor_user_id: actor || null,
        action: action || null,
      });
      setMorePages((prev) => [...prev, ...page.entries]);
      setCursor(page.next_cursor);
    } catch {
      toast.error("Couldn't load more activity.");
    } finally {
      setLoadingMore(false);
    }
  }

  const members = membersQuery.data ?? [];
  const hasFilters = actor !== "" || action !== "";

  return (
    <div className="flex flex-col gap-4">
      {/* Filters bar */}
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-muted-foreground">
            By user
          </span>
          <Select
            value={actor || ANY_ACTOR}
            onValueChange={(v) => {
              setActor(v === ANY_ACTOR ? "" : v);
              resetPages();
            }}
          >
            <SelectTrigger className="w-52" aria-label="Filter by user">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ANY_ACTOR}>Anyone</SelectItem>
              {members.map((m) => (
                <SelectItem key={m.user_id} value={m.user_id}>
                  {memberLabel(m)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-muted-foreground">
            By action
          </span>
          <Select
            value={action || ALL_ACTIONS}
            onValueChange={(v) => {
              setAction(v === ALL_ACTIONS ? "" : v);
              resetPages();
            }}
          >
            <SelectTrigger className="w-56" aria-label="Filter by action">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_ACTIONS}>All actions</SelectItem>
              {Object.entries(ACTION_LABELS).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {hasFilters ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setActor("");
              setAction("");
              resetPages();
            }}
          >
            Clear filters
          </Button>
        ) : null}
      </div>

      {/* List */}
      {firstQuery.isPending ? (
        <div className="flex flex-col divide-y divide-border">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex items-center justify-between gap-4 py-3">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-3 w-20 shrink-0" />
            </div>
          ))}
        </div>
      ) : firstQuery.isError ? (
        <p className="py-6 text-center text-sm text-destructive" role="alert">
          Couldn't load activity. Please refresh and try again.
        </p>
      ) : (() => {
        const entries = [...firstQuery.data.entries, ...morePages];
        if (entries.length === 0) {
          return (
            <div className="py-10 text-center">
              <p className="text-sm font-medium text-foreground">
                No activity yet.
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Mutations show up here as they happen.
              </p>
            </div>
          );
        }
        return (
          <div className="flex flex-col">
            <div className="flex flex-col divide-y divide-border">
              {entries.map((entry) => (
                <ActivityRow key={entry.id} entry={entry} />
              ))}
            </div>
            {cursor ? (
              <div className="mt-4 flex justify-center">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void loadMore()}
                  disabled={loadingMore}
                >
                  {loadingMore ? "Loading…" : "Load more"}
                </Button>
              </div>
            ) : null}
          </div>
        );
      })()}
    </div>
  );
}
