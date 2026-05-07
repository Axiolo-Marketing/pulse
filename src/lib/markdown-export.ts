import type { Card, Client, ClientResponse } from "./supabase";
import type { Status } from "./status-suggest";

export interface UploadInfo {
  id: string;
  name: string;
  sizeBytes: number;
}

export interface ExportArgs {
  card: Card;
  client: Client;
  response: ClientResponse | undefined;
  status: Status;
  uploads: UploadInfo[]; // attachment summaries (no URLs — files live in admin)
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
  const { card, client, response, status, uploads } = args;

  const responseBody = renderResponseBody(card, response, uploads);

  return [
    `# ${card.title}`,
    "",
    `**Status:** ${status}`,
    "",
    `## Response from ${client.name}`,
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
      body = `**${v.selected ?? ""}**`;
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
      if (uploads.length === 0) {
        body = "_No files uploaded._";
      } else {
        const list = uploads
          .map((u) => `- \`${u.name}\` (${formatBytes(u.sizeBytes)})`)
          .join("\n");
        body =
          `**Files attached (${uploads.length}):**\n${list}\n\n` +
          `_Files live in the Pulse admin. Search for the file names above to locate them in your local archive._`;
      }
      break;
    default:
      body = "";
  }
  return body + noteSuffix;
}

// Combine multiple card blocks into one paste-ready markdown string.
export function renderEngagementMarkdown(blocks: string[]): string {
  return blocks.join("\n");
}
