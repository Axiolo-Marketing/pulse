import { Sparkles } from "lucide-react";

import {
  adminApi,
  type Card as CardModel,
  type ClientResponse,
  type Recipient,
  type UploadRow,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export function rcKey(recipientId: string, cardId: string): string {
  return `${recipientId}:${cardId}`;
}

export function recipientLabel(r: Recipient): string {
  return r.email || r.name || "Respondent";
}

/** "+N" badge for the engagement list: a visual cue that an engagement
 * has LLM-generated reactive-cards follow-ups (`ai_cards_count > 0`).
 * Same soft-secondary + `Sparkles` language as {@link AiFollowupBadge},
 * just without the per-card recipient/parent-card provenance lookup —
 * the list only has the aggregate count, not the individual cards.
 * Renders nothing at `count === 0`. */
export function AiFollowupCountBadge({
  count,
}: {
  count: number;
}): React.ReactElement | null {
  if (count <= 0) return null;
  const label = `${count} AI follow-up question${count === 1 ? "" : "s"} added`;
  return (
    <Badge variant="secondary" className="gap-1" title={label} aria-label={label}>
      <Sparkles />+{count}
    </Badge>
  );
}

/** Provenance badge for a reactive-cards follow-up (`card.source ===
 * "ai"`) — which recipient it's scoped to, and (if cheaply resolvable)
 * the question whose correction triggered it, as a tooltip. Renders
 * nothing for operator-authored cards. */
export function AiFollowupBadge({
  card,
  recipients,
  cards,
  responses,
}: {
  card: CardModel;
  recipients: Recipient[];
  cards: CardModel[];
  responses: ClientResponse[];
}): React.ReactElement | null {
  if (card.source !== "ai") return null;

  const recipient = recipients.find((r) => r.id === card.recipient_id);
  const triggerResponse = card.generated_from_response_id
    ? responses.find((r) => r.id === card.generated_from_response_id)
    : undefined;
  const parentCard = triggerResponse
    ? cards.find((c) => c.id === triggerResponse.card_id)
    : undefined;

  return (
    <Badge
      variant="secondary"
      className="gap-1"
      title={
        parentCard
          ? `Generated from a correction on "${parentCard.title}"`
          : "AI-generated follow-up card"
      }
    >
      <Sparkles />
      AI follow-up{recipient ? ` · for ${recipientLabel(recipient)}` : ""}
    </Badge>
  );
}

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function StateBadge({
  response,
}: {
  response?: ClientResponse;
}): React.ReactElement {
  const map: Record<string, { label: string; cls: string }> = {
    answered: { label: "Answered", cls: "bg-secondary text-secondary-foreground" },
    needs_edit: { label: "Needs edit", cls: "bg-warning-soft text-warning" },
    skipped: { label: "Skipped", cls: "bg-warning-soft text-warning" },
    viewed: { label: "Viewed", cls: "bg-muted text-muted-foreground" },
  };
  const { label, cls } = map[response?.state ?? ""] ?? {
    label: "Not viewed",
    cls: "bg-muted text-muted-foreground",
  };
  return (
    <span
      className={cn(
        "whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold",
        cls,
      )}
    >
      {label}
    </span>
  );
}

interface Value {
  confirmed?: boolean;
  correction?: string;
  selected?: string | string[];
  text?: string;
  url?: string;
  name?: string;
  email?: string;
  role?: string;
  note?: string;
}

function Muted({ children }: { children: React.ReactNode }): React.ReactElement {
  return <p className="italic text-muted-foreground">{children}</p>;
}

/** One recipient's answer to one card — value per response_type, plus any
 * file/voice uploads (downloaded through the admin session cookie). */
export function ResponseBody({
  card,
  response,
  uploads,
}: {
  card: CardModel;
  response?: ClientResponse;
  uploads: UploadRow[];
}): React.ReactElement {
  const files = uploads.filter((u) => u.kind === "file");
  const voices = uploads.filter((u) => u.kind === "voice");
  const v = (response?.response_value ?? {}) as Value;

  let content: React.ReactNode = null;
  if (!response || response.state === "not_started") {
    content = <Muted>Not yet viewed.</Muted>;
  } else if (response.state === "viewed") {
    content = <Muted>Opened, no response yet.</Muted>;
  } else if (response.state === "skipped") {
    content = <Muted>Skipped.</Muted>;
  } else {
    switch (card.response_type) {
      case "confirm-edit":
        content = v.confirmed ? (
          <p>Confirmed as written.</p>
        ) : (
          <div>
            <p className="text-muted-foreground">Edited:</p>
            <blockquote className="mt-1 whitespace-pre-wrap border-l-2 border-border pl-3">
              {v.correction}
            </blockquote>
          </div>
        );
        break;
      case "single-select":
        // A note-only answer (the deck's "Send note" path) has no
        // `selected`; the Note line below carries the whole response.
        content = v.selected ? (
          <p className="font-medium">{String(v.selected)}</p>
        ) : (
          <Muted>No option selected.</Muted>
        );
        break;
      case "multi-select": {
        const arr = Array.isArray(v.selected) ? v.selected : [];
        content = arr.length ? (
          <ul className="list-disc pl-5">
            {arr.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        ) : (
          <Muted>None selected.</Muted>
        );
        break;
      }
      case "short-text":
      case "long-text":
        content = <p className="whitespace-pre-wrap">{v.text ?? ""}</p>;
        break;
      case "document-link":
        content = v.url ? (
          <a
            href={v.url}
            target="_blank"
            rel="noreferrer"
            className="break-all text-primary hover:underline"
          >
            {v.url}
          </a>
        ) : (
          <Muted>—</Muted>
        );
        break;
      case "contact-share":
        content = (
          <div>
            <p className="font-medium">
              {v.name}
              {v.role ? ` (${v.role})` : ""}
            </p>
            <p className="text-muted-foreground">{v.email}</p>
          </div>
        );
        break;
      case "file-upload":
        content = files.length ? (
          <ul className="flex flex-col gap-1">
            {files.map((u) => (
              <li key={u.id}>
                <a
                  href={adminApi.uploadDownloadUrl(u.id)}
                  className="text-primary hover:underline"
                  download
                >
                  {u.file_name}
                </a>{" "}
                <span className="text-xs text-muted-foreground">
                  ({fmtSize(u.file_size_bytes)})
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <Muted>No files uploaded.</Muted>
        );
        break;
      default:
        content = null;
    }
  }

  return (
    <div className="text-sm text-foreground">
      {content}
      {v.note ? (
        <p className="mt-2 text-xs text-muted-foreground">
          <span className="font-semibold">Note:</span> {v.note}
        </p>
      ) : null}
      {voices.map((u) => (
        <div key={u.id} className="mt-3">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Voice answer
          </p>
          {/* eslint-disable-next-line jsx-a11y/media-has-caption */}
          <audio
            controls
            preload="metadata"
            src={adminApi.uploadDownloadUrl(u.id)}
            className="w-full max-w-sm"
          />
        </div>
      ))}
    </div>
  );
}
