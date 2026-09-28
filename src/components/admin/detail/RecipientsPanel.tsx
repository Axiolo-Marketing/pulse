import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookUser,
  Link2,
  MoreHorizontal,
  Send,
  Trash2,
  UserPlus,
} from "lucide-react";
import { toast } from "sonner";

import {
  adminApi,
  ApiError,
  clientsApi,
  type ClientContact,
  type Recipient,
} from "@/lib/api";
import { copyText, deckUrl } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";

import { Field } from "../form-parts";
import { RespondentPicker, isEmail, type PickedRespondent } from "../RespondentPicker";
import { ConfirmDialog } from "./EngagementDialogs";
import { recipientLabel } from "./parts";

function shortDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

/** Where a respondent stands on the invite → answer path. */
function recipientStatus(r: Recipient): { label: string; tone: "muted" | "warn" | "ok" } {
  if (r.unsubscribed_at) return { label: "Unsubscribed", tone: "muted" };
  if (r.last_active_at) return { label: `Active ${shortDate(r.last_active_at)}`, tone: "ok" };
  if (r.invited_at) return { label: `Sent ${shortDate(r.invited_at)}`, tone: "muted" };
  return { label: "Not sent", tone: "warn" };
}

/** A respondent can be emailed if they have an address and haven't
 * unsubscribed. */
function canEmail(r: Recipient): boolean {
  return !!r.email && !r.unsubscribed_at;
}

/** Initial-letter avatar — shared with the per-card response rows. */
export function RecipientAvatar({
  r,
  className,
}: {
  r: Recipient;
  className?: string;
}): React.ReactElement {
  const initial = (recipientLabel(r)[0] ?? "?").toUpperCase();
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium text-foreground",
        className,
      )}
    >
      {initial}
    </span>
  );
}

function useInvalidateRecipients(engagementId: string): () => Promise<unknown> {
  const qc = useQueryClient();
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ["engagement", engagementId] }),
      qc.invalidateQueries({ queryKey: ["engagements"] }),
      qc.invalidateQueries({ queryKey: ["recipients", engagementId] }),
      qc.invalidateQueries({ queryKey: ["client-contacts"] }),
    ]);
}

// ── Send invites dialog ─────────────────────────────────────────────────────

function SendInvitesDialog({
  engagementId,
  recipients,
  initial,
  open,
  onOpenChange,
}: {
  engagementId: string;
  recipients: Recipient[];
  /** Pre-checked ids; defaults to everyone not yet emailed. */
  initial?: string[];
  open: boolean;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const invalidate = useInvalidateRecipients(engagementId);
  const eligible = recipients.filter(canEmail);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (open) {
      setSelected(
        new Set(
          initial ?? eligible.filter((r) => !r.invited_at).map((r) => r.id),
        ),
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const mut = useMutation({
    mutationFn: () => adminApi.sendInvites(engagementId, [...selected]),
    onSuccess: (res) => {
      void invalidate();
      onOpenChange(false);
      toast.success(
        `Invite${res.sent === 1 ? "" : "s"} sent to ${res.sent} ${res.sent === 1 ? "person" : "people"}.`,
        res.skipped.length
          ? { description: `${res.skipped.length} skipped (unsubscribed).` }
          : undefined,
      );
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.detail : "Couldn't send invites."),
  });

  function toggle(id: string, on: boolean): void {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  const allOn = eligible.length > 0 && eligible.every((r) => selected.has(r.id));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Email the deck link</DialogTitle>
          <DialogDescription>
            Each person gets their own private link. People you've already
            emailed get it again.
          </DialogDescription>
        </DialogHeader>
        {eligible.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nobody here can be emailed. Add a respondent with an email first.
          </p>
        ) : (
          <div className="rounded-lg border border-border">
            <label className="flex cursor-pointer items-center gap-3 border-b border-border bg-muted/40 px-4 py-2.5 text-xs font-medium text-muted-foreground">
              <Checkbox
                checked={allOn}
                onCheckedChange={(v) =>
                  setSelected(v ? new Set(eligible.map((r) => r.id)) : new Set())
                }
              />
              Select all
            </label>
            <ul className="max-h-72 divide-y divide-border overflow-y-auto">
              {eligible.map((r) => {
                const st = recipientStatus(r);
                return (
                  <li key={r.id}>
                    <label className="flex cursor-pointer items-center gap-3 px-4 py-2.5">
                      <Checkbox
                        checked={selected.has(r.id)}
                        onCheckedChange={(v) => toggle(r.id, v === true)}
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-foreground">
                          {r.name || r.email}
                        </span>
                        {r.name ? (
                          <span className="block truncate text-xs text-muted-foreground">
                            {r.email}
                          </span>
                        ) : null}
                      </span>
                      <span
                        className={cn(
                          "shrink-0 text-xs",
                          st.tone === "warn" ? "text-amber-700" : "text-muted-foreground",
                        )}
                      >
                        {st.label}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => mut.mutate()}
            disabled={selected.size === 0 || mut.isPending}
          >
            <Send />
            {selected.size === 0
              ? "Send"
              : `Send to ${selected.size} ${selected.size === 1 ? "person" : "people"}`}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Client contacts dialog ──────────────────────────────────────────────────

function ClientContactsDialog({
  clientId,
  clientName,
  open,
  onOpenChange,
}: {
  clientId: string;
  clientName?: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["client-contacts", clientId],
    queryFn: () => clientsApi.listContacts(clientId),
    enabled: open,
  });
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<ClientContact | null>(null);

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["client-contacts"] });

  const saveMut = useMutation({
    mutationFn: () =>
      clientsApi.saveContact(clientId, {
        email: email.trim(),
        name: name.trim() || null,
        role: role.trim() || null,
      }),
    onSuccess: () => {
      setEmail("");
      setName("");
      setRole("");
      setError(null);
      void invalidate();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.detail : "Couldn't save contact."),
  });
  const removeMut = useMutation({
    mutationFn: (c: ClientContact) => clientsApi.removeContact(clientId, c.id),
    onSuccess: () => {
      setRemoving(null);
      void invalidate();
    },
    onError: () => {
      setRemoving(null);
      toast.error("Couldn't remove that contact.");
    },
  });

  const contacts = q.data ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{clientName ? `${clientName} contacts` : "Client contacts"}</DialogTitle>
          <DialogDescription>
            People you can pick as respondents on any of this client's
            engagements. Anyone you add as a respondent is saved here too.
          </DialogDescription>
        </DialogHeader>

        <div className="rounded-lg border border-border">
          {q.isPending ? (
            <p className="px-4 py-4 text-sm text-muted-foreground">Loading…</p>
          ) : contacts.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-muted-foreground">
              No saved contacts yet.
            </p>
          ) : (
            <ul className="max-h-64 divide-y divide-border overflow-y-auto">
              {contacts.map((c) => (
                <li key={c.id} className="flex items-center gap-3 px-4 py-2.5">
                  <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-medium">
                    {((c.name || c.email)[0] ?? "?").toUpperCase()}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-foreground">
                      {c.name || c.email}
                      {c.role ? (
                        <span className="ml-1.5 font-normal text-muted-foreground">
                          · {c.role}
                        </span>
                      ) : null}
                    </div>
                    {c.name ? (
                      <div className="truncate text-xs text-muted-foreground">{c.email}</div>
                    ) : null}
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8 text-muted-foreground hover:text-destructive"
                    aria-label={`Remove ${c.email}`}
                    onClick={() => setRemoving(c)}
                  >
                    <Trash2 />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <form
          id="client-contact-form"
          className="grid gap-3 sm:grid-cols-3"
          onSubmit={(e) => {
            e.preventDefault();
            if (!isEmail(email)) {
              setError("Enter a valid email.");
              return;
            }
            saveMut.mutate();
          }}
        >
          <Field id="cc-email" label="Email">
            <Input
              id="cc-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="name@client.com"
            />
          </Field>
          <Field id="cc-name" label="Name" optional>
            <Input id="cc-name" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field id="cc-role" label="Role" optional>
            <Input id="cc-role" value={role} onChange={(e) => setRole(e.target.value)} />
          </Field>
          {error ? (
            <p className="text-sm text-destructive sm:col-span-3" role="alert">
              {error}
            </p>
          ) : null}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Done
          </Button>
          <Button type="submit" form="client-contact-form" disabled={saveMut.isPending}>
            <UserPlus />
            Save contact
          </Button>
        </DialogFooter>

        <ConfirmDialog
          open={removing !== null}
          onOpenChange={(o) => {
            if (!o) setRemoving(null);
          }}
          title="Remove this contact?"
          description={`${removing?.email ?? "They"} will no longer be suggested. Anyone already on an engagement stays there.`}
          confirmLabel="Remove contact"
          destructive
          pending={removeMut.isPending}
          onConfirm={() => {
            if (removing) removeMut.mutate(removing);
          }}
        />
      </DialogContent>
    </Dialog>
  );
}

// ── Panel ───────────────────────────────────────────────────────────────────

export function RecipientsPanel({
  engagementId,
  clientId,
  clientName,
  recipients,
  hasCards,
}: {
  engagementId: string;
  clientId?: string;
  clientName?: string;
  recipients: Recipient[];
  hasCards: boolean;
}): React.ReactElement {
  const invalidate = useInvalidateRecipients(engagementId);
  const [adding, setAdding] = useState(false);
  const [picked, setPicked] = useState<PickedRespondent[]>([]);
  const [removing, setRemoving] = useState<Recipient | null>(null);
  const [sendOpen, setSendOpen] = useState(false);
  const [sendInitial, setSendInitial] = useState<string[] | undefined>();
  const [contactsOpen, setContactsOpen] = useState(false);

  const contactsQ = useQuery({
    queryKey: ["client-contacts", clientId],
    queryFn: () => clientsApi.listContacts(clientId!),
    enabled: adding && !!clientId,
  });

  const addMut = useMutation({
    mutationFn: async () => {
      const failed: string[] = [];
      for (const p of picked) {
        try {
          await adminApi.addRecipient(engagementId, {
            email: p.email,
            name: p.name ?? undefined,
          });
        } catch (err) {
          failed.push(
            err instanceof ApiError && err.status === 409
              ? `${p.email} (already added)`
              : p.email,
          );
        }
      }
      return { added: picked.length - failed.length, failed };
    },
    onSuccess: ({ added, failed }) => {
      void invalidate();
      setPicked([]);
      setAdding(false);
      if (added) {
        toast.success(`Added ${added} ${added === 1 ? "respondent" : "respondents"}.`, {
          description: "Nobody was emailed. Copy their link or send when ready.",
        });
      }
      if (failed.length) toast.error(`Couldn't add ${failed.join(", ")}.`);
    },
  });

  const removeMut = useMutation({
    mutationFn: (recipientId: string) =>
      adminApi.removeRecipient(engagementId, recipientId),
    onSuccess: () => {
      setRemoving(null);
      void invalidate();
    },
    onError: () => {
      setRemoving(null);
      toast.error("Couldn't remove that respondent.");
    },
  });

  async function copyLink(r: Recipient): Promise<void> {
    const url = deckUrl(r.token);
    try {
      await copyText(url);
      toast.success(`Link copied for ${recipientLabel(r)}`, { description: url });
    } catch {
      toast.info("Copy this link", { description: url, duration: 15_000 });
    }
  }

  function openSend(ids?: string[]): void {
    setSendInitial(ids);
    setSendOpen(true);
  }

  const notSent = recipients.filter((r) => canEmail(r) && !r.invited_at).length;
  const sendBlockedReason = hasCards ? null : "Add a card before emailing the deck.";

  return (
    <section className="rounded-lg border border-border">
      <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">
          Respondents
          <span className="ml-2 font-normal text-muted-foreground">
            {recipients.length}
          </span>
        </h2>
        <div className="-mr-2 flex items-center gap-0.5">
          {recipients.length > 0 ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1.5 text-muted-foreground hover:text-foreground"
              disabled={!hasCards}
              title={sendBlockedReason ?? "Email the deck link"}
              onClick={() => openSend()}
            >
              <Send />
              Send…
            </Button>
          ) : null}
          {!adding ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1.5 text-muted-foreground hover:text-foreground"
              onClick={() => setAdding(true)}
            >
              <UserPlus />
              Add
            </Button>
          ) : null}
          {clientId ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-7 text-muted-foreground hover:text-foreground"
                  aria-label="More respondent actions"
                >
                  <MoreHorizontal />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => setContactsOpen(true)}>
                  <BookUser />
                  Client contacts…
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : null}
        </div>
      </div>

      {notSent > 0 && hasCards ? (
        <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/40 px-4 py-2 text-xs text-muted-foreground">
          <span>
            {notSent} {notSent === 1 ? "person hasn't" : "people haven't"} been emailed.
          </span>
          <button
            type="button"
            className="font-medium text-foreground underline-offset-2 hover:underline"
            onClick={() => openSend()}
          >
            Review & send
          </button>
        </div>
      ) : null}

      <ul className="divide-y divide-border">
        {recipients.map((r) => {
          const total = r.total_cards;
          const pct = total > 0 ? Math.round((r.completed_count / total) * 100) : 0;
          const st = recipientStatus(r);
          return (
            <li key={r.id} className="flex items-center gap-3 px-4 py-3">
              <RecipientAvatar r={r} />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-foreground">
                  {r.name || recipientLabel(r)}
                </div>
                <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                  <span className="h-1 w-10 shrink-0 overflow-hidden rounded-full bg-muted">
                    <span
                      className="block h-full rounded-full bg-foreground"
                      style={{ width: `${pct}%` }}
                    />
                  </span>
                  <span className="tabular-nums">
                    {r.completed_count}/{total}
                  </span>
                  <span aria-hidden="true">·</span>
                  <span
                    className={cn(
                      "truncate",
                      st.tone === "warn" && "text-amber-700",
                    )}
                  >
                    {st.label}
                  </span>
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 text-muted-foreground hover:text-foreground"
                title="Copy link"
                aria-label={`Copy link for ${recipientLabel(r)}`}
                onClick={() => void copyLink(r)}
              >
                <Link2 />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 text-muted-foreground hover:text-foreground"
                disabled={!hasCards || !canEmail(r)}
                title={
                  sendBlockedReason ??
                  (!canEmail(r)
                    ? "Unsubscribed from emails"
                    : r.invited_at
                      ? "Resend invite email"
                      : "Send invite email")
                }
                aria-label={`${r.invited_at ? "Resend" : "Send"} invite to ${recipientLabel(r)}`}
                onClick={() => openSend([r.id])}
              >
                <Send />
              </Button>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="-mr-2 size-8 text-muted-foreground hover:text-foreground"
                    aria-label={`More actions for ${recipientLabel(r)}`}
                  >
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    variant="destructive"
                    onSelect={() => setRemoving(r)}
                  >
                    <Trash2 />
                    Remove
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </li>
          );
        })}
        {recipients.length === 0 && !adding ? (
          <li className="px-4 py-6 text-center text-sm text-muted-foreground">
            No respondents yet.
            <Button
              variant="link"
              className="h-auto p-0 pl-1"
              onClick={() => setAdding(true)}
            >
              Add people
            </Button>
          </li>
        ) : null}
      </ul>

      {adding ? (
        <div className="flex flex-col gap-2 border-t border-border p-4">
          <RespondentPicker
            id="add-respondents"
            contacts={contactsQ.data ?? []}
            value={picked}
            onChange={setPicked}
            exclude={recipients.map((r) => r.email ?? "").filter(Boolean)}
            disabled={addMut.isPending}
            autoFocus
          />
          <p className="text-xs text-muted-foreground">
            Adding doesn't email anyone. Copy their link or send when you're ready.
          </p>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setAdding(false);
                setPicked([]);
              }}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={picked.length === 0 || addMut.isPending}
              onClick={() => addMut.mutate()}
            >
              {picked.length > 1 ? `Add ${picked.length} people` : "Add"}
            </Button>
          </div>
        </div>
      ) : null}

      <SendInvitesDialog
        engagementId={engagementId}
        recipients={recipients}
        initial={sendInitial}
        open={sendOpen}
        onOpenChange={setSendOpen}
      />
      {clientId ? (
        <ClientContactsDialog
          clientId={clientId}
          clientName={clientName}
          open={contactsOpen}
          onOpenChange={setContactsOpen}
        />
      ) : null}
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(o) => {
          if (!o) setRemoving(null);
        }}
        title="Remove this respondent?"
        description={`This deletes ${removing ? recipientLabel(removing) : "this respondent"}'s magic link and every answer, file, and voice note they submitted. This can't be undone.`}
        confirmLabel="Remove respondent"
        destructive
        pending={removeMut.isPending}
        onConfirm={() => {
          if (removing) removeMut.mutate(removing.id);
        }}
      />
    </section>
  );
}
