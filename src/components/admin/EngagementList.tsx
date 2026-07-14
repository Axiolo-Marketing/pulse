import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronsUpDown } from "lucide-react";
import { useNavigate } from "react-router-dom";

import {
  adminApi,
  clientsApi,
  type ClientSummary,
  type EngagementSummary,
} from "@/lib/api";
import {
  engagementStatus,
  STATUS_LABELS,
  type EngagementStatus,
} from "@/lib/engagement-status";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { AiFollowupCountBadge } from "./detail/parts";
import { AdminError, AdminLoading } from "./states";
import { NewEngagementDialog } from "./NewEngagementDialog";

const UNASSIGNED = "__unassigned__";

const STATUS_BADGE: Record<EngagementStatus, string> = {
  complete: "bg-success-soft text-success",
  in_progress: "bg-secondary text-secondary-foreground",
  waiting: "bg-muted text-muted-foreground",
};

/** Colour a per-recipient answered/total badge: green when they've answered
 * every card, tinted while in progress, muted when they haven't started. */
function recipientBadgeClass(answered: number, total: number): string {
  if (total > 0 && answered >= total) return "bg-success-soft text-success";
  if (answered > 0) return "bg-secondary text-secondary-foreground";
  return "bg-muted text-muted-foreground";
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger className="w-full" aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/** Searchable client filter — a Popover + Command combobox so a long client
 * list stays easy to scan and pick from. "all" clears the filter. */
function ClientFilter({
  value,
  onChange,
  clients,
}: {
  value: string;
  onChange: (v: string) => void;
  clients: ClientSummary[];
}): React.ReactElement {
  const [open, setOpen] = useState(false);
  const selected = clients.find((c) => c.id === value);
  const label = value === "all" ? "All clients" : (selected?.name ?? "All clients");

  function pick(v: string): void {
    onChange(v);
    setOpen(false);
  }

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-muted-foreground">Client</span>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            aria-label="Client"
            className="h-9 w-full justify-between font-normal"
          >
            <span className="truncate">{label}</span>
            <ChevronsUpDown className="size-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          className="w-[var(--radix-popover-trigger-width)] min-w-52 p-0"
          align="start"
        >
          <Command>
            <CommandInput placeholder="Search clients…" />
            <CommandList>
              <CommandEmpty>No clients found.</CommandEmpty>
              <CommandGroup>
                <CommandItem value="All clients" onSelect={() => pick("all")}>
                  All clients
                  <Check
                    className={cn(
                      "ml-auto size-4",
                      value === "all" ? "opacity-100" : "opacity-0",
                    )}
                  />
                </CommandItem>
                {clients.map((c) => (
                  <CommandItem
                    key={c.id}
                    value={c.name}
                    onSelect={() => pick(c.id)}
                  >
                    <span className="truncate">{c.name}</span>
                    <Check
                      className={cn(
                        "ml-auto size-4",
                        value === c.id ? "opacity-100" : "opacity-0",
                      )}
                    />
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  );
}

export function EngagementList(): React.ReactElement {
  const navigate = useNavigate();
  const engQ = useQuery({
    queryKey: ["engagements"],
    queryFn: () => adminApi.listEngagements(),
  });
  const clientsQ = useQuery({
    queryKey: ["clients"],
    queryFn: () => clientsApi.list(),
  });

  const [status, setStatus] = useState<"all" | EngagementStatus>("all");
  const [client, setClient] = useState("all");
  const [owner, setOwner] = useState("all");
  const [sort, setSort] = useState<"name" | "last_active" | "status">("name");
  const [newOpen, setNewOpen] = useState(false);

  if (engQ.isPending || clientsQ.isPending) return <AdminLoading />;
  if (engQ.isError) {
    return (
      <AdminError
        title="Couldn't load engagements"
        body="Please refresh and try again."
      />
    );
  }

  const summaries = engQ.data;
  const clients = clientsQ.data ?? [];
  const owners = [
    ...new Set(summaries.map((s) => s.owner_name).filter(Boolean) as string[]),
  ].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));
  const hasUnassigned = summaries.some((s) => !s.owner_name);

  const filtered = summaries.filter((s) => {
    if (status !== "all" && engagementStatus(s) !== status) return false;
    if (client !== "all" && s.client_id !== client) return false;
    if (owner === UNASSIGNED && s.owner_name) return false;
    if (owner !== "all" && owner !== UNASSIGNED && s.owner_name !== owner)
      return false;
    return true;
  });

  const STATUS_RANK: Record<EngagementStatus, number> = {
    complete: 0,
    in_progress: 1,
    waiting: 2,
  };
  const sorted = [...filtered].sort((a, b) => {
    if (sort === "name") {
      return (a.engagement_name ?? "").localeCompare(b.engagement_name ?? "");
    }
    if (sort === "last_active") {
      return (
        new Date(b.last_active_at ?? 0).getTime() -
        new Date(a.last_active_at ?? 0).getTime()
      );
    }
    return STATUS_RANK[engagementStatus(a)] - STATUS_RANK[engagementStatus(b)];
  });

  // Group by client, keeping client sections alphabetical.
  const byClient = new Map<string, EngagementSummary[]>();
  for (const s of sorted) {
    byClient.set(s.client_id, [...(byClient.get(s.client_id) ?? []), s]);
  }
  const sections = [...byClient.entries()].sort((a, b) =>
    (a[1][0]?.client_name ?? "").localeCompare(b[1][0]?.client_name ?? ""),
  );

  return (
    <main className="mx-auto w-full max-w-4xl px-4 py-6">
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-bold text-foreground">Engagements</h1>
        <Button type="button" onClick={() => setNewOpen(true)}>
          New engagement
        </Button>
      </div>
      <NewEngagementDialog open={newOpen} onOpenChange={setNewOpen} />

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <FilterSelect
          label="Status"
          value={status}
          onChange={(v) => setStatus(v as never)}
          options={[
            { value: "all", label: "All statuses" },
            { value: "complete", label: "Complete" },
            { value: "in_progress", label: "In progress" },
            { value: "waiting", label: "Waiting" },
          ]}
        />
        <ClientFilter value={client} onChange={setClient} clients={clients} />
        <FilterSelect
          label="Owner"
          value={owner}
          onChange={setOwner}
          options={[
            { value: "all", label: "All owners" },
            ...owners.map((o) => ({ value: o, label: o })),
            ...(hasUnassigned
              ? [{ value: UNASSIGNED, label: "Unassigned" }]
              : []),
          ]}
        />
        <FilterSelect
          label="Sort"
          value={sort}
          onChange={(v) => setSort(v as never)}
          options={[
            { value: "name", label: "Name (A–Z)" },
            { value: "last_active", label: "Last active" },
            { value: "status", label: "Status" },
          ]}
        />
      </div>

      {sections.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border bg-card px-4 py-10 text-center text-sm text-muted-foreground">
          {summaries.length === 0
            ? "No engagements yet."
            : "No engagements match these filters."}
        </p>
      ) : (
        <div className="flex flex-col gap-6">
          {sections.map(([clientId, rows]) => (
            <section key={clientId}>
              <div className="mb-2 flex items-baseline gap-2">
                <h2 className="text-sm font-semibold text-foreground">
                  {rows[0]?.client_name}
                </h2>
                <span className="text-xs text-muted-foreground">
                  {rows.length} engagement{rows.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className="overflow-hidden rounded-lg border border-border bg-card">
                {rows.map((s) => {
                  const st = engagementStatus(s);
                  return (
                    <button
                      key={s.id}
                      type="button"
                      onClick={() => navigate(`/client/${s.id}`)}
                      className="flex w-full flex-col gap-2 border-b border-border px-4 py-3 text-left last:border-b-0 hover:bg-muted"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate font-medium text-foreground">
                            {s.engagement_name || "Untitled engagement"}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {s.owner_name || s.owner_email || "—"} ·{" "}
                            {s.total_cards} question
                            {s.total_cards === 1 ? "" : "s"} · updated{" "}
                            {fmtDate(s.last_active_at)}
                          </div>
                        </div>
                        <div className="flex shrink-0 items-center gap-1.5">
                          <AiFollowupCountBadge count={s.ai_cards_count} />
                          <span
                            className={cn(
                              "whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-semibold",
                              STATUS_BADGE[st],
                            )}
                          >
                            {STATUS_LABELS[st]}
                          </span>
                        </div>
                      </div>

                      {s.total_cards === 0 ? (
                        <p className="text-xs italic text-muted-foreground">
                          No cards in this deck yet.
                        </p>
                      ) : s.recipients.length === 0 ? (
                        <p className="text-xs italic text-muted-foreground">
                          No respondents yet.
                        </p>
                      ) : (
                        <ul className="flex flex-col gap-1">
                          {s.recipients.map((r) => (
                            <li
                              key={r.id}
                              className="flex items-center gap-2 text-sm"
                            >
                              <span className="min-w-0 truncate text-foreground">
                                {r.email || r.name || "Respondent"}
                              </span>
                              <span
                                className={cn(
                                  "shrink-0 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold",
                                  recipientBadgeClass(r.answered, s.total_cards),
                                )}
                                title={`${r.answered} of ${s.total_cards} answered`}
                              >
                                {r.answered}/{s.total_cards}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </main>
  );
}
