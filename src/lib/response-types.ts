/** Human labels for each card response type, shared by every admin view. */
export const RESPONSE_TYPE_LABELS: Record<string, string> = {
  "confirm-edit": "Confirm or edit",
  "single-select": "Single choice",
  "multi-select": "Multiple choice",
  "short-text": "Short text",
  "long-text": "Long text",
  "document-link": "Link",
  "contact-share": "Contact",
  "file-upload": "File upload",
  grid: "Grid (page)",
};

export function responseTypeLabel(type: string): string {
  return RESPONSE_TYPE_LABELS[type] ?? type;
}
