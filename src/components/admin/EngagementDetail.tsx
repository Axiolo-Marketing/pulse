import { useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Copy,
  Download,
  ExternalLink,
  Eye,
  FileText,
  LoaderCircle,
  MoreHorizontal,
  Pencil,
  Plus,
  RotateCcw,
  Sparkles,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import Markdown from "react-markdown";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import {
  adminApi,
  ApiError,
  type Card as CardModel,
  type EngagementDetail as EngagementDetailData,
  type Recipient,
} from "@/lib/api";
import {
  buildCardMarkdown,
  buildEngagementExport,
  exportFilename,
} from "@/lib/markdown-export";
import { formatTimestamp } from "@/lib/format-time";
import { cn } from "@/lib/utils";
import { copyText } from "@/lib/clipboard";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

import { BRIEF_TEMPLATE } from "./detail/brief-template";
import { CardEditorDialog } from "./detail/CardEditorDialog";
import {
  ConfirmDialog,
  EditEngagementDialog,
} from "./detail/EngagementDialogs";
import { RecipientAvatar, RecipientsPanel } from "./detail/RecipientsPanel";
import {
  AiFollowupBadge,
  rcKey,
  recipientLabel,
  ResponseBody,
  isTranscribing,
  StateBadge,
  type TranscriptionControls,
} from "./detail/parts";
import { AdminError, AdminLoading } from "./states";

function buildMarkdown(detail: EngagementDetailData): string {
  return buildEngagementExport(
    detail,
    (id) => adminApi.uploadDownloadUrl(id),
    recipientLabel,
  );
}

/** Confirm-dialog bodies that spell out what gets erased, from the detail
 * payload the page already holds. */
function confirmCopy(detail: EngagementDetailData): {
  resetDescription: string;
  deleteDescription: string;
} {
  const { engagement } = detail;
  const label = [engagement.name, engagement.engagement_name]
    .filter(Boolean)
    .join(" · ");
  const plural = (n: number, w: string): string => `${n} ${w}${n === 1 ? "" : "s"}`;
  const responses = detail.responses.filter(
    (r) => r.state === "answered" || r.state === "skipped",
  ).length;
  const files = detail.uploads.filter((u) => u.kind !== "voice").length;
  const voices = detail.uploads.filter((u) => u.kind === "voice").length;

  const cleared: string[] = [];
  if (responses > 0) cleared.push(plural(responses, "response"));
  if (files > 0) cleared.push(plural(files, "uploaded file"));
  if (voices > 0) cleared.push(plural(voices, "voice note"));
  const resetDescription = [
    `Reset all answers for ${label}?`,
    cleared.length > 0
      ? `This clears ${cleared.join(" and ")}, returning every card to unanswered.`
      : "There are no answers to clear yet.",
    "The cards and respondent links stay the same, so they can start over. This can't be undone.",
  ].join(" ");

  const removed =
    detail.cards.length > 0
      ? [plural(detail.cards.length, "card"), plural(responses, "response")]
      : [];
  if (detail.cards.length > 0) {
    if (files > 0) removed.push(plural(files, "uploaded file"));
    if (voices > 0) removed.push(plural(voices, "voice note"));
  }
  const deleteDescription = [
    `Delete ${label}?`,
    removed.length > 0
      ? `This will permanently remove ${removed.join(", ")}, along with ${plural(detail.recipients.length, "respondent")} and their links.`
      : "No cards have been added to this engagement yet.",
    "This can't be undone.",
  ].join(" ");
  return { resetDescription, deleteDescription };
}

const RESPONSE_TYPE_LABELS: Record<string, string> = {
  "confirm-edit": "Confirm or edit",
  "single-select": "Single choice",
  "multi-select": "Multiple choice",
  "short-text": "Short text",
  "long-text": "Long text",
  "document-link": "Link",
  "contact-share": "Contact",
  "file-upload": "File upload",
};

const MARKDOWN_PROSE = cn(
  "text-sm text-foreground",
  "[&>*:first-child]:mt-0 [&_p]:mt-2 [&_hr]:my-3 [&_hr]:border-border",
  "[&_h1]:mt-3 [&_h1]:text-base [&_h1]:font-semibold",
  "[&_h2]:mt-3 [&_h2]:text-sm [&_h2]:font-semibold",
  "[&_h3]:mt-2 [&_h3]:text-sm [&_h3]:font-medium",
  "[&_ul]:mt-1 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:mt-1 [&_ol]:list-decimal [&_ol]:pl-5",
  "[&_blockquote]:mt-2 [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-3 [&_blockquote]:italic [&_blockquote]:text-muted-foreground",
  "[&_a]:underline [&_strong]:font-semibold",
  "[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.85em]",
);

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
  const hasBrief = !!brief?.trim();
  // An empty brief opens pre-filled with the starter outline.
  function startEditing(): void {
    setText(hasBrief ? (brief ?? "") : BRIEF_TEMPLATE);
    setEditing(true);
  }
  async function copyBrief(): Promise<void> {
    try {
      await copyText(brief ?? "");
      toast.success("Brief copied as Markdown.");
    } catch {
      toast.error("Couldn't copy the brief.");
    }
  }
  const mut = useMutation({
    mutationFn: () =>
      adminApi.updateEngagement(engagementId, { brief: text.trim() || null }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["engagement", engagementId] });
      setEditing(false);
    },
    onError: () => toast.error("Couldn't save the brief."),
  });

  return (
    <section className="rounded-lg border border-border">
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-foreground">Brief</h2>
        {!editing ? (
          <div className="-mr-2 flex items-center gap-1">
            {hasBrief ? (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 gap-1.5 text-muted-foreground hover:text-foreground"
                onClick={() => void copyBrief()}
              >
                <Copy />
                Copy as Markdown
              </Button>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              className="h-7 gap-1.5 text-muted-foreground hover:text-foreground"
              onClick={startEditing}
            >
              <Pencil />
              Edit
            </Button>
          </div>
        ) : null}
      </div>
      {editing ? (
        <div className="p-4">
          <Textarea
            rows={8}
            value={text}
            autoFocus
            onChange={(e) => setText(e.target.value)}
            placeholder="Operator notes for this engagement (Markdown)…"
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button size="sm" onClick={() => mut.mutate()} disabled={mut.isPending}>
              Save
            </Button>
          </div>
        </div>
      ) : hasBrief ? (
        // Briefs are authored as Markdown — render them in a capped scroll
        // box so a long brief doesn't push the sidebar out of reach.
        <div className={cn("max-h-80 overflow-y-auto px-4 py-3", MARKDOWN_PROSE)}>
          <Markdown>{brief}</Markdown>
        </div>
      ) : (
        <div className="flex flex-col items-center gap-2 px-4 py-6 text-center">
          <FileText className="size-5 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            No brief yet. A one-page narrative of who the client is and what
            you're validating, copyable as Markdown.
          </p>
          <Button variant="outline" size="sm" onClick={startEditing}>
            <Plus />
            Write brief
          </Button>
        </div>
      )}
    </section>
  );
}

function CardBlock({
  card,
  position,
  detail,
  transcription,
  onEdit,
  onDelete,
  onCopyAnswer,
}: {
  card: CardModel;
  position: number;
  detail: EngagementDetailData;
  transcription: TranscriptionControls;
  onEdit: () => void;
  onDelete: () => void;
  onCopyAnswer: (r: Recipient) => void;
}): React.ReactElement {
  return (
    <article className="rounded-lg border border-border">
      <div className="flex items-start gap-3 px-4 pt-4 pb-3">
        <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-muted text-xs font-medium tabular-nums text-muted-foreground">
          {position}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-medium text-foreground">{card.title}</h3>
            <AiFollowupBadge
              card={card}
              recipients={detail.recipients}
              cards={detail.cards}
              responses={detail.responses}
            />
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">{card.question}</p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {card.category ? (
              <span className="rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
                {card.category}
              </span>
            ) : null}
            <span className="rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
              {RESPONSE_TYPE_LABELS[card.response_type] ?? card.response_type}
            </span>
          </div>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="-mt-1 -mr-2 size-8 text-muted-foreground hover:text-foreground"
              aria-label={`Actions for card ${position}`}
            >
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-40">
            <DropdownMenuItem onSelect={onEdit}>
              <Pencil />
              Edit card
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem variant="destructive" onSelect={onDelete}>
              <Trash2 />
              Delete card
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {detail.recipients.length > 0 ? (
        <ul className="divide-y divide-border border-t border-border bg-muted/30">
          {detail.recipients.map((r) => {
            const response = detail.responses.find(
              (x) => x.recipient_id === r.id && x.card_id === card.id,
            );
            const uploads = detail.uploads.filter(
              (x) => x.recipient_id === r.id && x.card_id === card.id,
            );
            const ts = response?.answered_at ?? response?.viewed_at ?? null;
            return (
              <li key={rcKey(r.id, card.id)} className="flex gap-3 px-4 py-3">
                <RecipientAvatar r={r} className="mt-0.5 size-6 bg-background text-[11px] ring-1 ring-border" />
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium text-foreground">
                      {r.name || recipientLabel(r)}
                    </span>
                    {r.name && r.email ? (
                      <span className="truncate text-xs text-muted-foreground">
                        {r.email}
                      </span>
                    ) : null}
                    <StateBadge response={response} />
                    <span className="ml-auto flex items-center gap-1">
                      {ts ? (
                        <span className="text-xs text-muted-foreground">
                          {formatTimestamp(ts)}
                        </span>
                      ) : null}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="-my-1 size-7 text-muted-foreground hover:text-foreground"
                        title="Copy this answer as Markdown"
                        aria-label={`Copy ${recipientLabel(r)}'s answer as Markdown`}
                        onClick={() => onCopyAnswer(r)}
                      >
                        <Copy />
                      </Button>
                    </span>
                  </div>
                  <ResponseBody
                    card={card}
                    response={response}
                    uploads={uploads}
                    transcription={transcription}
                  />
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </article>
  );
}

function Stat({
  label,
  value,
  hint,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
}): React.ReactElement {
  return (
    <div className="bg-background px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums tracking-tight text-foreground">
        {value}
      </div>
      {hint ? <div className="text-xs text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

function FeatureChip({
  on,
  children,
}: {
  on: boolean | undefined;
  children: React.ReactNode;
}): React.ReactElement | null {
  if (!on) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">
      {children}
    </span>
  );
}

/** Phone-sized preview of the respondent deck, embedded from
 * `<base>preview?e=<id>` (real deck screens + the org's branding; nothing is
 * saved). "Restart" remounts the frame to start again from card 1. */
function PreviewDialog({
  engagementId,
  open,
  onOpenChange,
}: {
  engagementId: string;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const [run, setRun] = useState(0);
  const base = import.meta.env.BASE_URL;
  const src = `${base.endsWith("/") ? base : `${base}/`}preview?e=${encodeURIComponent(engagementId)}`;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="gap-4 sm:max-w-[460px]">
        <DialogHeader>
          <DialogTitle>Preview</DialogTitle>
          <DialogDescription>
            What respondents see, with your branding. Answers here aren't
            saved and nobody is notified.
          </DialogDescription>
        </DialogHeader>
        <div className="mx-auto w-full max-w-[390px] overflow-hidden rounded-[1.75rem] border-[6px] border-foreground/90 bg-background shadow-lg">
          <iframe
            key={run}
            src={src}
            title="Respondent deck preview"
            className="block h-[clamp(460px,calc(100dvh-16rem),720px)] w-full"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setRun((n) => n + 1)}>
            <RotateCcw />
            Restart
          </Button>
          <Button variant="outline" asChild>
            <a href={src} target="_blank" rel="noreferrer">
              <ExternalLink />
              Open in new tab
            </a>
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function EngagementDetail(): React.ReactElement {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["engagement", id],
    queryFn: () => adminApi.getEngagement(id),
    // Poll while any voice answer is still being transcribed.
    refetchInterval: (query) =>
      query.state.data?.uploads.some((u) => isTranscribing(u))
        ? 2000
        : false,
  });

  const [editOpen, setEditOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [cardEditor, setCardEditor] = useState<
    { open: boolean; card?: CardModel } | null
  >(null);
  const [deletingCard, setDeletingCard] = useState<CardModel | null>(null);

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
  const transcribeMut = useMutation({
    mutationFn: (uploadId: string) => adminApi.transcribeUpload(uploadId),
    // The server claimed the row, but a refetch can race its commit. Mark
    // it pending locally so the spinner shows and polling starts at once.
    onSuccess: (_res, uploadId) => {
      qc.setQueryData<EngagementDetailData>(["engagement", id], (d) =>
        d && {
          ...d,
          uploads: d.uploads.map((u) =>
            u.id === uploadId
              ? {
                  ...u,
                  transcript_status: "pending",
                  transcript_error: null,
                  transcribed_at: new Date().toISOString(),
                }
              : u,
          ),
        },
      );
    },
    onError: (err) =>
      toast.error(
        err instanceof ApiError ? err.detail : "Couldn't start transcription.",
      ),
  });
  // Per-card parse errors come back newline-joined in the 400's detail;
  // show them as an inline list beside the Import control.
  const [importErrors, setImportErrors] = useState<string[] | null>(null);
  const importMut = useMutation({
    mutationFn: (markdown: string) => adminApi.importMarkdownCards(id, markdown),
    onMutate: () => setImportErrors(null),
    onSuccess: ({ created }) => {
      void qc.invalidateQueries({ queryKey: ["engagement", id] });
      toast.success(
        `${created.length} card${created.length === 1 ? "" : "s"} imported`,
      );
    },
    onError: (err) => {
      const detail =
        err instanceof ApiError ? err.detail : "Could not import that Markdown.";
      const lines = detail
        .split("\n")
        .map((l) => l.trim())
        .filter(Boolean);
      setImportErrors(lines.length ? lines : [detail]);
    },
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

  async function copyAll(): Promise<void> {
    try {
      await copyText(buildMarkdown(detail));
      toast.success("Responses copied as Markdown.");
    } catch {
      toast.error("Couldn't copy — use Download instead.");
    }
  }
  function download(): void {
    const blob = new Blob([buildMarkdown(detail)], {
      type: "text/markdown;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = exportFilename(engagement);
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }
  async function copyAnswer(card: CardModel, r: Recipient): Promise<void> {
    try {
      await copyText(
        buildCardMarkdown(
          detail,
          card,
          r,
          (uid) => adminApi.uploadDownloadUrl(uid),
          recipientLabel,
        ),
      );
      toast.success(`Copied ${recipientLabel(r)}'s answer.`);
    } catch {
      toast.error("Couldn't copy.");
    }
  }

  // ── Summary numbers ──
  const recipients = detail.recipients;
  const doneRecipients = recipients.filter(
    (r) => r.total_cards > 0 && r.completed_count >= r.total_cards,
  ).length;
  const answeredCount = detail.responses.filter(
    (r) => r.state === "answered" || r.state === "skipped" || r.state === "needs_edit",
  ).length;
  const expectedAnswers = recipients.reduce((n, r) => n + r.total_cards, 0);
  const lastActive = recipients
    .map((r) => r.last_active_at)
    .filter((t): t is string => Boolean(t))
    .sort()
    .at(-1);
  const title = engagement.engagement_name?.trim() || "Untitled engagement";
  const { resetDescription, deleteDescription } = confirmCopy(detail);
  const transcriptionControls: TranscriptionControls = {
    enabled:
      !!detail.transcription_available && !!engagement.transcription_enabled,
    onTranscribe: (uploadId) => transcribeMut.mutate(uploadId),
  };

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8">
      <nav aria-label="Breadcrumb" className="mb-4 flex items-center gap-1.5 text-sm text-muted-foreground">
        <Link to="/" className="hover:text-foreground">
          Engagements
        </Link>
        <ChevronRight className="size-3.5" aria-hidden="true" />
        <span className="truncate">{engagement.name}</span>
      </nav>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">
            {title}
          </h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
            <span>{engagement.name}</span>
            <span aria-hidden="true">·</span>
            <span>
              Created{" "}
              {new Date(engagement.created_at).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
                year: "numeric",
              })}
            </span>
            <FeatureChip on={engagement.voice_enabled}>Voice</FeatureChip>
            <FeatureChip on={engagement.reminders_enabled}>Reminders</FeatureChip>
            <FeatureChip on={engagement.transcription_enabled}>Transcripts</FeatureChip>
            <FeatureChip on={engagement.reactive_cards_enabled}>
              <Sparkles className="size-3" />
              AI follow-ups
            </FeatureChip>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPreviewOpen(true)}
            disabled={cards.length === 0}
            title={cards.length === 0 ? "Add cards to preview the deck" : undefined}
          >
            <Eye />
            Preview
          </Button>
          <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
            <Pencil />
            Edit
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="sm">
                <Download />
                Export
                <ChevronDown className="opacity-60" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-48">
              <DropdownMenuItem onSelect={() => void copyAll()}>
                <Copy />
                Copy as Markdown
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={download}>
                <Download />
                Download .md
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon" className="size-8" aria-label="More actions">
                <MoreHorizontal />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-48">
              <DropdownMenuItem onSelect={() => setResetOpen(true)}>
                <RotateCcw />
                Reset all answers
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => setDeleteOpen(true)}>
                <Trash2 />
                Delete engagement
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="mb-8 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-4">
        <Stat
          label="Respondents complete"
          value={`${doneRecipients} / ${recipients.length}`}
        />
        <Stat label="Questions" value={cards.length} />
        <Stat
          label="Answers"
          value={`${answeredCount} / ${expectedAnswers}`}
          hint={
            expectedAnswers > 0
              ? `${Math.round((answeredCount / expectedAnswers) * 100)}% complete`
              : undefined
          }
        />
        <Stat
          label="Last activity"
          value={
            <span className="text-base">
              {lastActive ? formatTimestamp(lastActive) : "None yet"}
            </span>
          }
        />
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <section className="order-2 flex flex-col gap-3 lg:order-1">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-foreground">
              Questions & responses
              <span className="ml-2 font-normal text-muted-foreground">
                {cards.length}
              </span>
            </h2>
            <div className="flex gap-2">
              <label className="inline-flex">
                <input
                  type="file"
                  accept=".md,.markdown,.txt,text/markdown,text/plain"
                  className="peer sr-only"
                  disabled={importMut.isPending}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) void file.text().then((t) => importMut.mutate(t));
                  }}
                />
                <span className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-input bg-background px-3 text-sm font-medium shadow-xs hover:bg-accent peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50">
                  {importMut.isPending ? (
                    <LoaderCircle className="size-4 animate-spin" />
                  ) : (
                    <Upload className="size-4" />
                  )}
                  {importMut.isPending ? "Importing…" : "Import"}
                </span>
              </label>
              <Button size="sm" onClick={() => setCardEditor({ open: true })}>
                <Plus />
                Add card
              </Button>
            </div>
          </div>

          {importErrors ? (
            <div
              role="alert"
              className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm"
            >
              <div className="flex items-center justify-between gap-2">
                <strong className="font-medium text-destructive">Import failed</strong>
                <Button
                  variant="ghost"
                  size="icon"
                  className="-my-1 -mr-2 size-7 text-muted-foreground"
                  aria-label="Dismiss import errors"
                  onClick={() => setImportErrors(null)}
                >
                  <X />
                </Button>
              </div>
              {importErrors.length > 1 ? (
                <ul className="mt-1 list-disc space-y-0.5 pl-5 text-foreground">
                  {importErrors.map((l, i) => (
                    <li key={i}>{l}</li>
                  ))}
                </ul>
              ) : (
                <p className="mt-1 text-foreground">{importErrors[0]}</p>
              )}
            </div>
          ) : null}

          {cards.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border px-4 py-12 text-center">
              <FileText className="size-6 text-muted-foreground" />
              <p className="text-sm font-medium text-foreground">No questions yet</p>
              <p className="text-sm text-muted-foreground">
                Add a card, or import a deck from Markdown.
              </p>
            </div>
          ) : (
            cards.map((card, i) => (
              <CardBlock
                key={card.id}
                card={card}
                position={i + 1}
                detail={detail}
                transcription={transcriptionControls}
                onEdit={() => setCardEditor({ open: true, card })}
                onDelete={() => setDeletingCard(card)}
                onCopyAnswer={(r) => void copyAnswer(card, r)}
              />
            ))
          )}
        </section>

        <aside className="order-1 flex flex-col gap-4 lg:sticky lg:top-20 lg:order-2">
          <RecipientsPanel
            engagementId={id}
            clientId={engagement.client_id}
            clientName={engagement.name}
            recipients={detail.recipients}
            hasCards={detail.cards.length > 0}
          />
          <BriefCard engagementId={id} brief={engagement.brief} />
        </aside>
      </div>

      <PreviewDialog
        engagementId={id}
        open={previewOpen}
        onOpenChange={setPreviewOpen}
      />
      <EditEngagementDialog
        engagementId={id}
        engagement={engagement}
        transcriptionAvailable={!!detail.transcription_available}
        open={editOpen}
        onOpenChange={setEditOpen}
      />
      <ConfirmDialog
        open={resetOpen}
        onOpenChange={setResetOpen}
        title="Reset all answers?"
        description={resetDescription}
        confirmLabel="Reset answers"
        destructive
        pending={resetMut.isPending}
        onConfirm={() => resetMut.mutate()}
      />
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete this engagement?"
        description={deleteDescription}
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
