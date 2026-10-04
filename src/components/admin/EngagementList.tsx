import { useEffect, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Check,
  ChevronsUpDown,
  ExternalLink,
  Link2,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";

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
import {
  clientRollupText,
  loadListControls,
  saveListControls,
  sortComparator,
} from "@/lib/engagement-list";
import { copyText, deckUrl } from "@/lib/clipboard";
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
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
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
import { ConfirmDialog } from "./detail/EngagementDialogs";
import { AdminError, AdminLoading } from "./states";
import { NewEngagementDialog } from "./NewEngagementDialog";

const UNASSIGNED = "__unassigned__";

/** Status dot colour — the label always rides alongside, so status is never
 * a colour-only signal. */
const STATUS_DOT: Record<EngagementStatus, string> = {
  complete: "bg-success",
  in_progress: "bg-brand",
  waiting: "bg-neutral-300",
};

function fmtDate(iso: string | null): string {
  if (!iso) return "Never";
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
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="h-9 w-full sm:w-40" aria-label={label}>
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
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label="Client"
          className="h-9 w-full justify-between px-3 font-normal sm:w-44"
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
  );
}

/** Copy a respondent's deck link. The list payload carries no tokens, so the
 * recipients are fetched on demand. One respondent → a single click copies;
 * several → a menu to pick whose link. */
function CopyLinkAction({ s }: { s: EngagementSummary }): React.ReactElement {
  const qc = useQueryClient();
  const count = s.recipients.length;

  async function copyFor(recipientId: string | null): Promise<void> {
    const urlPromise = qc
      .fetchQuery({
        queryKey: ["recipients", s.id],
        queryFn: () => adminApi.listRecipients(s.id),
        staleTime: 30_000,
      })
      .then((recipients) => {
        const r = recipientId
          ? recipients.find((x) => x.id === recipientId)
          : recipients[0];
        if (!r) throw new Error("recipient not found");
        return deckUrl(r.token);
      });

    let url: string;
    try {
      url = await copyText(urlPromise);
    } catch {
      // Fetch failed, or the browser refused the clipboard. If we did get the
      // URL, surface it so the operator can still copy it by hand.
      const fallback = await urlPromise.catch(() => null);
      if (fallback) {
        toast.info("Copy this link", { description: fallback, duration: 15_000 });
      } else {
        toast.error("Couldn't load the link.");
      }
      return;
    }
    const who = s.recipients.find((r) => r.id === recipientId);
    const label = who?.email || who?.name;
    toast.success(label ? `Link copied for ${label}` : "Link copied", {
      description: url,
    });
  }

  if (count === 0) {
    return (
      <Button
        variant="ghost"
        size="icon"
        className="size-8 text-muted-foreground"
        disabled
        title="No respondents yet"
        aria-label="No respondents yet"
      >
        <Link2 />
      </Button>
    );
  }

  if (count === 1) {
    return (
      <Button
        variant="ghost"
        size="icon"
        className="size-8 text-muted-foreground hover:text-foreground"
        title="Copy respondent link"
        aria-label="Copy respondent link"
        onClick={() => void copyFor(s.recipients[0]?.id ?? null)}
      >
        <Link2 />
      </Button>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-8 text-muted-foreground hover:text-foreground"
          title="Copy a respondent link"
          aria-label="Copy a respondent link"
        >
          <Link2 />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-56">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
          Copy link for…
        </DropdownMenuLabel>
        {s.recipients.map((r) => (
          <DropdownMenuItem key={r.id} onSelect={() => void copyFor(r.id)}>
            <span className="truncate">{r.email || r.name || "Respondent"}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function RowMenu({
  s,
  onDelete,
}: {
  s: EngagementSummary;
  onDelete: () => void;
}): React.ReactElement {
  const navigate = useNavigate();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="size-8 text-muted-foreground hover:text-foreground"
          aria-label="More actions"
        >
          <MoreHorizontal />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-44">
        <DropdownMenuItem onSelect={() => navigate(`/client/${s.id}`)}>
          <ExternalLink />
          Open
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={onDelete}>
          <Trash2 />
          Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function StatusCell({
  s,
  compact,
}: {
  s: EngagementSummary;
  compact?: boolean;
}): React.ReactElement {
  const st = engagementStatus(s);
  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap",
        compact ? "gap-1.5 text-xs" : "gap-2 text-sm text-foreground",
      )}
    >
      <span
        aria-hidden="true"
        className={cn("size-2 shrink-0 rounded-full", STATUS_DOT[st])}
      />
      {STATUS_LABELS[st]}
    </span>
  );
}

/** Completed-of-total respondents, with a thin progress bar. */
function ProgressCell({ s }: { s: EngagementSummary }): React.ReactElement {
  const done = s.completed_recipients;
  const total = s.recipients_count;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div
      className="flex min-w-16 flex-col gap-1.5"
      title={`${done} of ${total} respondent${total === 1 ? "" : "s"} complete`}
    >
      <span className="text-sm tabular-nums text-foreground">
        {done} / {total}{" "}
        <span className="text-muted-foreground">
          respondent{total === 1 ? "" : "s"}
        </span>
      </span>
      <span className="h-1 w-full overflow-hidden rounded-full bg-muted">
        <span
          className="block h-full rounded-full bg-foreground"
          style={{ width: `${pct}%` }}
        />
      </span>
    </div>
  );
}

/** What a delete erases, from the counts the list already carries. */
function deleteDescription(s: EngagementSummary | null): string {
  if (!s) return "";
  const label = [s.client_name, s.engagement_name].filter(Boolean).join(" · ");
  const what =
    s.total_cards > 0
      ? `This will permanently remove ${s.total_cards} card${s.total_cards === 1 ? "" : "s"} and every respondent's answers (${s.recipients_count} respondent${s.recipients_count === 1 ? "" : "s"}), plus any uploaded files.`
      : "No cards have been added to this engagement yet.";
  return `Delete ${label}? ${what} This can't be undone.`;
}

export function EngagementList(): React.ReactElement {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const engQ = useQuery({
    queryKey: ["engagements"],
    queryFn: () => adminApi.listEngagements(),
  });
  const clientsQ = useQuery({
    queryKey: ["clients"],
    queryFn: () => clientsApi.list(),
  });

  // Filters live in module/sessionStorage-backed state so they survive a
  // trip to a detail page and back.
  const [controls] = useState(loadListControls);
  const [query, setQuery] = useState(controls.query);
  const [status, setStatus] = useState(controls.status);
  const [clientFilter, setClient] = useState(controls.client);
  const [ownerFilter, setOwner] = useState(controls.owner);
  const [sort, setSort] = useState(controls.sort);
  useEffect(() => {
    saveListControls({
      query,
      status,
      client: clientFilter,
      owner: ownerFilter,
      sort,
    });
  }, [query, status, clientFilter, ownerFilter, sort]);
  const [newOpen, setNewOpen] = useState(false);
  const [deleting, setDeleting] = useState<EngagementSummary | null>(null);

  const deleteMut = useMutation({
    mutationFn: (id: string) => adminApi.deleteEngagement(id),
    onSuccess: async () => {
      setDeleting(null);
      toast.success("Engagement deleted.");
      await qc.invalidateQueries({ queryKey: ["engagements"] });
    },
    onError: () => {
      setDeleting(null);
      toast.error("Couldn't delete the engagement.");
    },
  });

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
  // A restored filter can point at a client/owner that no longer exists here
  // (deleted, or the operator switched orgs) — treat it as "all" rather than
  // silently hiding every row behind a control that reads "All clients".
  const client =
    clientFilter === "all" || clients.some((c) => c.id === clientFilter)
      ? clientFilter
      : "all";
  const owner =
    ownerFilter === "all" ||
    (ownerFilter === UNASSIGNED && hasUnassigned) ||
    owners.includes(ownerFilter)
      ? ownerFilter
      : "all";

  const q = query.trim().toLowerCase();
  const filtered = summaries.filter((s) => {
    if (status !== "all" && engagementStatus(s) !== status) return false;
    if (client !== "all" && s.client_id !== client) return false;
    if (owner === UNASSIGNED && s.owner_name) return false;
    if (owner !== "all" && owner !== UNASSIGNED && s.owner_name !== owner)
      return false;
    if (q) {
      const haystack = [
        s.engagement_name,
        s.client_name,
        s.owner_name,
        ...s.recipients.flatMap((r) => [r.email, r.name]),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(q)) return false;
    }
    return true;
  });

  const sorted = [...filtered].sort(sortComparator(sort));

  // One section per client, alphabetical. The rollup is computed from the
  // client's full (pre-filter) engagements; a client with no engagements at
  // all still shows while no filter is narrowing the view.
  const membersByClient = new Map<string, EngagementSummary[]>();
  for (const s of summaries) {
    membersByClient.set(s.client_id, [...(membersByClient.get(s.client_id) ?? []), s]);
  }
  const visibleByClient = new Map<string, EngagementSummary[]>();
  for (const s of sorted) {
    visibleByClient.set(s.client_id, [...(visibleByClient.get(s.client_id) ?? []), s]);
  }
  const noFilters =
    status === "all" && owner === "all" && client === "all" && !q;
  const knownIds = new Set(clients.map((c) => c.id));
  const sectionClients = [
    ...clients,
    // Defensive: an engagement whose client isn't in the clients list.
    ...summaries
      .filter((s) => !knownIds.has(s.client_id))
      .map((s) => ({ id: s.client_id, name: s.client_name, created_at: "" }))
      .filter((c, i, arr) => arr.findIndex((x) => x.id === c.id) === i),
  ];
  const sections = sectionClients
    .map((c) => ({
      id: c.id,
      name: c.name,
      members: membersByClient.get(c.id) ?? [],
      rows: visibleByClient.get(c.id) ?? [],
    }))
    .filter((c) => c.rows.length > 0 || (c.members.length === 0 && noFilters))
    .sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    );

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8">
      <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            Engagements
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {summaries.length} engagement{summaries.length === 1 ? "" : "s"}{" "}
            across {clients.length} client{clients.length === 1 ? "" : "s"}
          </p>
        </div>
        <Button type="button" onClick={() => setNewOpen(true)}>
          <Plus />
          New engagement
        </Button>
      </div>
      <NewEngagementDialog open={newOpen} onOpenChange={setNewOpen} />

      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search engagements, clients, respondents…"
            aria-label="Search engagements"
            className="h-9 pl-9"
          />
        </div>
        <div className="grid grid-cols-2 gap-2 sm:flex">
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
              { value: "name", label: "Sort: Name" },
              { value: "last_active", label: "Sort: Last active" },
              { value: "status", label: "Sort: Status" },
            ]}
          />
        </div>
      </div>

      {sections.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border px-4 py-12 text-center text-sm text-muted-foreground">
          {summaries.length === 0
            ? "No engagements yet. Create one to get started."
            : "No engagements match these filters."}
        </p>
      ) : (
        <div className="flex flex-col gap-8">
          {sections.map(({ id: clientId, name: clientName, members, rows }) => (
            <section key={clientId} aria-labelledby={`client-${clientId}`}>
              <div className="mb-2 flex items-baseline gap-2 px-1">
                <h2
                  id={`client-${clientId}`}
                  className="text-sm font-semibold text-foreground"
                >
                  {clientName}
                </h2>
                <span className="text-xs text-muted-foreground">
                  {clientRollupText(members)}
                </span>
              </div>
              {rows.length === 0 ? (
                <p className="rounded-lg border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
                  No engagements for this client yet.
                </p>
              ) : null}
              <ul
                className={cn(
                  "divide-y divide-border overflow-hidden rounded-lg border border-border",
                  rows.length === 0 && "hidden",
                )}
              >
                {rows.map((s) => {
                  const owner = s.owner_name || s.owner_email;
                  return (
                    <li
                      key={s.id}
                      onClick={() => navigate(`/client/${s.id}`)}
                      className="flex cursor-pointer items-center gap-4 px-4 py-3.5 transition-colors hover:bg-muted/40"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <Link
                            to={`/client/${s.id}`}
                            onClick={(e) => e.stopPropagation()}
                            className="truncate font-medium text-foreground hover:underline"
                          >
                            {s.engagement_name || "Untitled engagement"}
                          </Link>
                          <AiFollowupCountBadge count={s.ai_cards_count} />
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
                          {/* Phones: status + progress fold into this line. */}
                          <span className="sm:hidden">
                            <StatusCell s={s} compact />
                          </span>
                          <span aria-hidden="true" className="sm:hidden">·</span>
                          <span className="sm:hidden tabular-nums">
                            {s.completed_recipients}/{s.recipients_count} done
                          </span>
                          <span aria-hidden="true" className="sm:hidden">·</span>
                          <span>
                            {s.total_cards} question{s.total_cards === 1 ? "" : "s"}
                          </span>
                          {owner ? (
                            <>
                              <span aria-hidden="true">·</span>
                              <span className="truncate">{owner}</span>
                            </>
                          ) : null}
                          <span aria-hidden="true" className="hidden md:inline">·</span>
                          <span className="hidden md:inline">
                            {s.last_active_at
                              ? `Active ${fmtDate(s.last_active_at)}`
                              : "No activity yet"}
                          </span>
                        </div>
                      </div>
                      <div className="hidden w-28 shrink-0 sm:block">
                        <StatusCell s={s} />
                      </div>
                      <div className="hidden w-32 shrink-0 sm:block">
                        <ProgressCell s={s} />
                      </div>
                      <div
                        className="-mr-2 flex shrink-0 items-center gap-0.5"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <CopyLinkAction s={s} />
                        <RowMenu s={s} onDelete={() => setDeleting(s)} />
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(o) => {
          if (!o) setDeleting(null);
        }}
        title="Delete this engagement?"
        description={deleteDescription(deleting)}
        confirmLabel="Delete engagement"
        destructive
        pending={deleteMut.isPending}
        onConfirm={() => {
          if (deleting) deleteMut.mutate(deleting.id);
        }}
      />
    </main>
  );
}
