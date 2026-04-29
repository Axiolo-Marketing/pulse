import type { Card, Client, ClientResponse } from "./supabase";
import type { Status } from "./status-suggest";

export interface UploadInfo {
  id: string;
  name: string;
  signedUrl: string;
}

export interface ExportArgs {
  card: Card;
  client: Client;
  response: ClientResponse | undefined;
  status: Status;
  uploads: UploadInfo[]; // resolved signed URLs for this card's files
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
  if (response.state === "skipped") {
    return "_Skipped._";
  }

  const v = (response.response_value ?? {}) as ResponseValueShape;

  switch (card.response_type) {
    case "confirm-edit":
      if (v.confirmed) {
        return "Confirmed as written.";
      }
      return [
        "Edited:",
        "",
        ...(v.correction ?? "")
          .split("\n")
          .map((line) => `> ${line}`),
      ].join("\n");

    case "single-select":
      return `**${v.selected ?? ""}**`;

    case "multi-select": {
      const arr = Array.isArray(v.selected) ? v.selected : [];
      if (arr.length === 0) return "_None selected._";
      return arr.map((s) => `- ${s}`).join("\n");
    }

    case "short-text":
    case "long-text":
      return v.text ?? "";

    case "document-link":
      return v.url ? `<${v.url}>` : "";

    case "contact-share":
      return [
        `**${v.name ?? ""}**${v.role ? ` (${v.role})` : ""}`,
        v.email ?? "",
      ]
        .filter(Boolean)
        .join("\n");

    case "file-upload":
      if (uploads.length === 0) return "_No files uploaded._";
      return uploads.map((u) => `- [${u.name}](${u.signedUrl})`).join("\n");

    default:
      return "";
  }
}

// Combine multiple card blocks into one paste-ready markdown string.
export function renderEngagementMarkdown(blocks: string[]): string {
  return blocks.join("\n");
}
