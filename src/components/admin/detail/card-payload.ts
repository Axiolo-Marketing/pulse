import type { Card, ResponseType } from "@/lib/api";

export function hasOptions(type: ResponseType): boolean {
  return type === "single-select" || type === "multi-select";
}

/** One option per non-blank line. */
export function parseOptions(text: string): string[] {
  return text
    .split("\n")
    .map((o) => o.trim())
    .filter((o) => o.length > 0);
}

/** Inline-validation message for the options field, or null when fine.
 * Select cards need at least one option or the respondent has nothing to pick. */
export function optionsError(type: ResponseType, text: string): string | null {
  if (hasOptions(type) && parseOptions(text).length === 0) {
    return "Add at least one option (one per line).";
  }
  return null;
}

export interface CardFormValues {
  category: string;
  title: string;
  context: string;
  question: string;
  responseType: ResponseType;
  optionsText: string;
  defaultValue: string;
  skipAllowed: boolean;
  attachmentPath: string;
}

/** Fields to send on save. On edit, `default_value` and `options` are only
 * included when they apply to the card's type AND the user changed them, so
 * saving a legacy card never wipes data the form doesn't show. */
export function buildCardFields(
  v: CardFormValues,
  existing?: Card,
): {
  category: string;
  title: string;
  context: string;
  question: string;
  skip_allowed: boolean;
  attachment_path: string | null;
  options?: string[];
  default_value?: string | null;
} {
  const fields: ReturnType<typeof buildCardFields> = {
    category: v.category.trim(),
    title: v.title.trim(),
    context: v.context.trim(),
    question: v.question.trim(),
    skip_allowed: v.skipAllowed,
    attachment_path: v.attachmentPath.trim() || null,
  };
  if (hasOptions(v.responseType)) {
    fields.options = parseOptions(v.optionsText);
  }
  const dv = v.defaultValue.trim();
  if (existing) {
    if (v.responseType === "confirm-edit" && dv !== (existing.default_value ?? "").trim()) {
      fields.default_value = dv || null;
    }
  } else if (v.responseType === "confirm-edit") {
    fields.default_value = dv || null;
  }
  return fields;
}
