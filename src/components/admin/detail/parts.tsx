import {
  adminApi,
  type Card as CardModel,
  type ClientResponse,
  type Recipient,
  type UploadRow,
} from "@/lib/api";
import { cn } from "@/lib/utils";

export function rcKey(recipientId: string, cardId: string): string {
  return `${recipientId}:${cardId}`;
}

export function recipientLabel(r: Recipient): string {
  return r.email || r.name || "Respondent";
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
        content = <p className="font-medium">{String(v.selected ?? "")}</p>;
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
