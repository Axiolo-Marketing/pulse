import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Copy, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { adminApi, ApiError, type Recipient } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import { ConfirmDialog } from "./EngagementDialogs";
import { recipientLabel } from "./parts";

function recipientHint(r: Recipient): string {
  if (r.unsubscribed_at) return "Unsubscribed";
  if (r.last_active_at)
    return `Active ${new Date(r.last_active_at).toLocaleDateString()}`;
  if (r.invited_at)
    return `Invited ${new Date(r.invited_at).toLocaleDateString()}`;
  return "Not invited yet";
}

export function RecipientsPanel({
  engagementId,
  recipients,
}: {
  engagementId: string;
  recipients: Recipient[];
}): React.ReactElement {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Recipient | null>(null);

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["engagement", engagementId] });

  const addMut = useMutation({
    mutationFn: () =>
      adminApi.addRecipient(engagementId, {
        email,
        name: name.trim() || undefined,
      }),
    onSuccess: () => {
      setEmail("");
      setName("");
      setError(null);
      void invalidate();
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.detail : "Could not add."),
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

  function copyLink(token: string, id: string): void {
    void navigator.clipboard.writeText(
      `${window.location.origin}/?t=${token}`,
    );
    setCopiedId(id);
    window.setTimeout(() => setCopiedId((c) => (c === id ? null : c)), 1500);
  }

  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-baseline gap-2">
        <h2 className="text-sm font-semibold text-foreground">Respondents</h2>
        <span className="text-xs text-muted-foreground">
          {recipients.length}
        </span>
      </div>

      <div className="flex flex-col divide-y divide-border">
        {recipients.map((r) => (
          <div key={r.id} className="flex items-center gap-3 py-2.5">
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-foreground">
                {recipientLabel(r)}
              </div>
              <div className="text-xs text-muted-foreground">
                {recipientHint(r)} · {r.completed_count}/{r.total_cards}
              </div>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => copyLink(r.token, r.id)}
              className="gap-1.5 text-muted-foreground"
            >
              <Copy />
              {copiedId === r.id ? "Copied" : "Link"}
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setRemoving(r)}
              aria-label={`Remove ${recipientLabel(r)}`}
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 />
            </Button>
          </div>
        ))}
        {recipients.length === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">
            No respondents yet. Add one to send them the deck.
          </p>
        ) : null}
      </div>

      <form
        className="mt-3 flex flex-wrap items-end gap-2 border-t border-border pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          addMut.mutate();
        }}
      >
        <Input
          type="email"
          required
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="min-w-40 flex-1"
        />
        <Input
          type="text"
          placeholder="Name (optional)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="min-w-32 flex-1"
        />
        <Button type="submit" disabled={addMut.isPending}>
          Add respondent
        </Button>
        {error ? (
          <p className="w-full text-sm text-destructive" role="alert">
            {error}
          </p>
        ) : null}
      </form>

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
