import { useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ChevronLeft,
  Copy,
  Download,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
  Upload,
} from "lucide-react";
import Markdown from "react-markdown";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import {
  adminApi,
  type Card as CardModel,
  type EngagementDetail as EngagementDetailData,
} from "@/lib/api";
import {
  renderCardMarkdown,
  renderEngagementMarkdown,
} from "@/lib/markdown-export";
import { suggestStatus } from "@/lib/status-suggest";
import { formatTimestamp } from "@/lib/format-time";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { CardEditorDialog } from "./detail/CardEditorDialog";
import {
  ConfirmDialog,
  EditEngagementDialog,
} from "./detail/EngagementDialogs";
import { RecipientsPanel } from "./detail/RecipientsPanel";
import { rcKey, recipientLabel, ResponseBody, StateBadge } from "./detail/parts";
import { AdminError, AdminLoading } from "./states";

function buildMarkdown(detail: EngagementDetailData): string {
  const cards = [...detail.cards].sort((a, b) => a.order_index - b.order_index);
  const blocks: string[] = [];
  for (const r of detail.recipients) {
    for (const card of cards) {
      const response = detail.responses.find(
        (x) => x.recipient_id === r.id && x.card_id === card.id,
      );
      const ups = detail.uploads.filter(
        (x) => x.recipient_id === r.id && x.card_id === card.id,
      );
      blocks.push(
        renderCardMarkdown({
          card,
          client: detail.engagement,
          response,
          status: suggestStatus(card, response),
          uploads: ups.map((u) => ({
            id: u.id,
            name: u.file_name,
            sizeBytes: u.file_size_bytes,
            url: adminApi.uploadDownloadUrl(u.id),
            kind: u.kind,
          })),
          recipientLabel: recipientLabel(r),
        }),
      );
    }
  }
  return renderEngagementMarkdown(blocks);
}

function BriefCard({
  engagementId,
  brief,
}: {
  engagementId: string;
  brief: string | null;
}): React.ReactElement {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(brief ?? "");
  const mut = useMutation({
    mutationFn: () =>
      adminApi.updateEngagement(engagementId, { brief: text.trim() || null }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["engagement", engagementId] });
      setEditing(false);
    },
    onError: () => toast.error("Couldn't save the brief."),
  });

  if (!editing) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">Brief</h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setText(brief ?? "");
              setEditing(true);
            }}
          >
            <Pencil />
            Edit
          </Button>
        </div>
        {brief ? (
          // Briefs are authored as Markdown (the v1 template is a full MD
          // doc) — render them, capped like v1's 600px scroll box, so the
          // cards & responses below stay within reach.
          <div
            className={cn(
              "max-h-[420px] overflow-y-auto rounded-md bg-muted/40 px-4 py-3 text-sm text-foreground",
              "[&>*:first-child]:mt-0 [&_p]:mt-2 [&_hr]:my-3 [&_hr]:border-border",
              "[&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-bold",
              "[&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-bold",
              "[&_h3]:mt-2 [&_h3]:text-sm [&_h3]:font-semibold",
              "[&_ul]:mt-1 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:mt-1 [&_ol]:list-decimal [&_ol]:pl-5",
              "[&_blockquote]:mt-2 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:italic [&_blockquote]:text-muted-foreground",
              "[&_a]:text-primary [&_a]:underline [&_strong]:font-semibold",
              "[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.85em]",
            )}
          >
            <Markdown>{brief}</Markdown>
          </div>
        ) : (
          <p className="text-sm italic text-muted-foreground">No brief yet.</p>
        )}
      </section>
    );
  }
  return (
    <section className="rounded-lg border border-border bg-card p-4">
      <Textarea
        rows={6}
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Operator notes for this engagement…"
      />
      <div className="mt-2 flex gap-2">
        <Button onClick={() => mut.mutate()} disabled={mut.isPending}>
          Save brief
        </Button>
        <Button variant="ghost" onClick={() => setEditing(false)}>
          Cancel
        </Button>
      </div>
    </section>
  );
}

function CardBlock({
  card,
  detail,
  onEdit,
  onDelete,
}: {
  card: CardModel;
  detail: EngagementDetailData;
  onEdit: () => void;
  onDelete: () => void;
}): React.ReactElement {
  return (
    <article className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Card {card.order_index} · {card.category}
          </p>
          <h3 className="mt-0.5 font-semibold text-foreground">{card.title}</h3>
        </div>
        <div className="flex shrink-0 gap-1">
          <Button variant="ghost" size="icon" onClick={onEdit} aria-label="Edit card">
            <Pencil />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            onClick={onDelete}
            aria-label="Delete card"
            className="text-muted-foreground hover:text-destructive"
          >
            <Trash2 />
          </Button>
        </div>
      </div>
      <p className="mt-2 text-sm font-medium text-foreground">{card.question}</p>

      <div className="mt-3 flex flex-col gap-3">
        {detail.recipients.map((r) => {
          const response = detail.responses.find(
            (x) => x.recipient_id === r.id && x.card_id === card.id,
          );
          const uploads = detail.uploads.filter(
            (x) => x.recipient_id === r.id && x.card_id === card.id,
          );
          const ts = response?.answered_at ?? response?.viewed_at ?? null;
          return (
            <div
              key={rcKey(r.id, card.id)}
              className="rounded-md border border-border bg-background p-3"
            >
              <div className="mb-1.5 flex items-center gap-2">
                <span className="truncate text-sm font-medium text-foreground">
                  {recipientLabel(r)}
                </span>
                <StateBadge response={response} />
                {ts ? (
                  <span className="ml-auto text-xs text-muted-foreground">
                    {formatTimestamp(ts)}
                  </span>
                ) : null}
              </div>
              <ResponseBody card={card} response={response} uploads={uploads} />
            </div>
          );
        })}
      </div>
    </article>
  );
}

export function EngagementDetail(): React.ReactElement {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["engagement", id],
    queryFn: () => adminApi.getEngagement(id),
  });

  const [editOpen, setEditOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [cardEditor, setCardEditor] = useState<
    { open: boolean; card?: CardModel } | null
  >(null);
  const [deletingCard, setDeletingCard] = useState<CardModel | null>(null);
  const [copied, setCopied] = useState(false);

  const resetMut = useMutation({
    mutationFn: () => adminApi.resetEngagement(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["engagement", id] });
      void qc.invalidateQueries({ queryKey: ["engagements"] });
      setResetOpen(false);
      toast.success("Answers reset.");
    },
    onError: () => toast.error("Couldn't reset answers."),
  });
  const deleteMut = useMutation({
    mutationFn: () => adminApi.deleteEngagement(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["engagements"] });
      navigate("/");
    },
    onError: () => {
      setDeleteOpen(false);
      toast.error("Couldn't delete the engagement.");
    },
  });
  const deleteCardMut = useMutation({
    mutationFn: (cardId: string) => adminApi.deleteCard(cardId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["engagement", id] });
      setDeletingCard(null);
    },
    onError: () => {
      setDeletingCard(null);
      toast.error("Couldn't delete the card.");
    },
  });
  const importMut = useMutation({
    mutationFn: (markdown: string) => adminApi.importMarkdownCards(id, markdown),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["engagement", id] });
      toast.success("Cards imported.");
    },
    onError: () => toast.error("Couldn't import that Markdown."),
  });

  if (q.isPending) return <AdminLoading />;
  if (q.isError) {
    return (
      <AdminError title="Couldn't load this engagement" body="Please go back and try again." />
    );
  }

  const detail = q.data;
  const { engagement } = detail;
  const cards = [...detail.cards].sort((a, b) => a.order_index - b.order_index);

  function copyAll(): void {
    void navigator.clipboard.writeText(buildMarkdown(detail));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }
  function download(): void {
    const blob = new Blob([buildMarkdown(detail)], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${engagement.engagement_name || engagement.name}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-6">
      <Button
        variant="ghost"
        size="sm"
        onClick={() => navigate("/")}
        className="mb-3 gap-1 text-muted-foreground"
      >
        <ChevronLeft />
        All engagements
      </Button>

      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-foreground">
            {engagement.name}
          </h1>
          {engagement.engagement_name ? (
            <p className="text-sm text-muted-foreground">
              {engagement.engagement_name}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
            <Pencil />
            Edit
          </Button>
          <Button variant="outline" size="sm" onClick={copyAll}>
            <Copy />
            {copied ? "Copied" : "Copy MD"}
          </Button>
          <Button variant="outline" size="sm" onClick={download}>
            <Download />
            Download
          </Button>
          <Button variant="outline" size="sm" onClick={() => setResetOpen(true)}>
            <RotateCcw />
            Reset
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setDeleteOpen(true)}
            className="text-destructive"
          >
            <Trash2 />
            Delete
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-5">
        <BriefCard engagementId={id} brief={engagement.brief} />
        <RecipientsPanel engagementId={id} recipients={detail.recipients} />

        <section className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-foreground">
              Cards & responses
            </h2>
            <div className="flex gap-2">
              <label className="inline-flex">
                <input
                  type="file"
                  accept=".md,.markdown,text/markdown"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) void file.text().then((t) => importMut.mutate(t));
                  }}
                />
                <span className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-md border border-input px-3 text-sm font-semibold hover:bg-accent">
                  <Upload className="size-4" />
                  Import
                </span>
              </label>
              <Button
                size="sm"
                onClick={() => setCardEditor({ open: true })}
              >
                <Plus />
                Add card
              </Button>
            </div>
          </div>

          {cards.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border bg-card px-4 py-10 text-center text-sm text-muted-foreground">
              No cards yet. Add one or import from Markdown.
            </p>
          ) : (
            cards.map((card) => (
              <CardBlock
                key={card.id}
                card={card}
                detail={detail}
                onEdit={() => setCardEditor({ open: true, card })}
                onDelete={() => setDeletingCard(card)}
              />
            ))
          )}
        </section>
      </div>

      <EditEngagementDialog
        engagementId={id}
        engagement={engagement}
        open={editOpen}
        onOpenChange={setEditOpen}
      />
      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title="Reset all answers?"
        description="This clears every recipient's responses, files, and voice notes. The cards and magic links stay. This can't be undone."
        confirmLabel="Reset answers"
        destructive
        pending={resetMut.isPending}
        onConfirm={() => resetMut.mutate()}
      />
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete this engagement?"
        description="This permanently removes the engagement, its cards, recipients, responses, and files. This can't be undone."
        confirmLabel="Delete engagement"
        destructive
        pending={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate()}
      />
      <ConfirmDialog
        open={deletingCard !== null}
        onOpenChange={(o) => {
          if (!o) setDeletingCard(null);
        }}
        title="Delete this card?"
        description="This removes the card and every recipient's answer to it. This can't be undone."
        confirmLabel="Delete card"
        destructive
        pending={deleteCardMut.isPending}
        onConfirm={() => {
          if (deletingCard) deleteCardMut.mutate(deletingCard.id);
        }}
      />
      {cardEditor ? (
        <CardEditorDialog
          engagementId={id}
          card={cardEditor.card}
          open={cardEditor.open}
          onOpenChange={(o) => setCardEditor(o ? cardEditor : null)}
        />
      ) : null}
    </main>
  );
}
