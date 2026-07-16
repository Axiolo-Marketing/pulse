import type { Card, ClientResponse, Engagement } from "./api";
import type { Status } from "./status-suggest";

export interface UploadInfo {
  id: string;
  name: string;
  sizeBytes: number;
  /** Direct download URL (admin-authed) so the export links straight to the
   * file / voice recording. Optional: when omitted the export falls back to
   * naming the file (the legacy behaviour). */
  url?: string;
  /** `"file"` attachments render as a file list; `"voice"` recordings render
   * as a separate "Voice answer" link. Omitted (legacy) uploads are treated
   * as files. */
  kind?: "file" | "voice";
}

export interface ExportArgs {
  card: Card;
  client: Engagement;
  response: ClientResponse | undefined;
  status: Status;
  /** Every upload for this card+recipient (both files and voice notes), each
   * with a download `url`. */
  uploads: UploadInfo[];
  /** Which recipient these answers belong to. Multi-respondent migration:
   * the export heading reads `Response from {client.name} — {recipientLabel}`.
   * Omit (or pass empty) to fall back to just the client name. */
  recipientLabel?: string;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface ResponseValueShape {
  confirmed?: boolean;
  correction?: string;
  selected?: string | string[];
  text?: string;
  url?: string;
  name?: string;
  email?: string;
  role?: string;
  file_ids?: string[];
  note?: string;
}

// Render one card's response as a ClickUp-ready markdown block per spec
// §14.3. Caller separates blocks with `---` rules.
export function renderCardMarkdown(args: ExportArgs): string {
  const { card, client, response, status, uploads, recipientLabel } = args;

  const responseBody = renderResponseBody(card, response, uploads);
  const heading = recipientLabel
    ? `## Response from ${client.name} — ${recipientLabel}`
    : `## Response from ${client.name}`;

  return [
    `# ${card.title}`,
    "",
    `**Status:** ${status}`,
    "",
    heading,
    responseBody,
    "",
    "## Original Context",
    card.context,
    "",
    "## Original Question",
    card.question,
    "",
    "---",
    "",
  ].join("\n");
}

function renderResponseBody(
  card: Card,
  response: ClientResponse | undefined,
  uploads: UploadInfo[]
): string {
  if (!response || response.state === "not_started") {
    return "_Not yet viewed._";
  }
  if (response.state === "viewed") {
    return "_Card opened, no response yet._";
  }
  const v = (response.response_value ?? {}) as ResponseValueShape;
  const noteSuffix = v.note ? `\n\n**Note:** ${v.note}` : "";
  // Legacy callers don't set `kind`; treat those as files.
  const files = uploads.filter((u) => u.kind !== "voice");
  const voices = uploads.filter((u) => u.kind === "voice");
  // Voice recordings can accompany any card type, so append them after the
  // response body (like the note) rather than switching on response_type.
  const voiceSuffix = voices.length
    ? `\n\n**Voice answer:** ${voices
        .map((u) => (u.url ? `[${u.name}](${u.url})` : u.name))
        .join(", ")}`
    : "";

  if (response.state === "skipped") {
    return v.note ? `_Skipped._${noteSuffix}` : "_Skipped._";
  }

  let body: string;
  switch (card.response_type) {
    case "confirm-edit":
      body = v.confirmed
        ? "Confirmed as written."
        : [
            "Edited:",
            "",
            ...(v.correction ?? "").split("\n").map((line) => `> ${line}`),
          ].join("\n");
      break;
    case "single-select":
      // A note-only answer (the deck's "Send note" path) has no `selected`;
      // the note suffix below carries the whole response.
      body = v.selected ? `**${v.selected}**` : "_No option selected._";
      break;
    case "multi-select": {
      const arr = Array.isArray(v.selected) ? v.selected : [];
      body = arr.length === 0 ? "_None selected._" : arr.map((s) => `- ${s}`).join("\n");
      break;
    }
    case "short-text":
    case "long-text":
      body = v.text ?? "";
      break;
    case "document-link":
      body = v.url ? `<${v.url}>` : "";
      break;
    case "contact-share":
      body = [
        `**${v.name ?? ""}**${v.role ? ` (${v.role})` : ""}`,
        v.email ?? "",
      ]
        .filter(Boolean)
        .join("\n");
      break;
    case "file-upload":
      if (files.length === 0) {
        body = "_No files uploaded._";
      } else {
        const list = files
          .map((u) =>
            u.url
              ? `- [${u.name}](${u.url}) (${formatBytes(u.sizeBytes)})`
              : `- \`${u.name}\` (${formatBytes(u.sizeBytes)})`,
          )
          .join("\n");
        // With links the reader can open the file directly; without them
        // (legacy) point back to the admin.
        const hint = files.some((u) => u.url)
          ? ""
          : `\n\n_Files live in the Pulse admin. Search for the file names above to locate them in your local archive._`;
        body = `**Files attached (${files.length}):**\n${list}${hint}`;
      }
      break;
    default:
      body = "";
  }
  return body + voiceSuffix + noteSuffix;
}

// Combine multiple card blocks into one paste-ready markdown string.
export function renderEngagementMarkdown(blocks: string[]): string {
  return blocks.join("\n");
}
