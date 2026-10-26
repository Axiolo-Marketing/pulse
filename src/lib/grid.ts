import type { Card } from "./api";

/** A grid answer (`{cells: {row: {col: value}}}`) laid out against the
 * card's rows/columns, for the admin table and the Markdown export. */
export interface GridTable {
  columns: string[];
  rows: { label: string; cells: string[] }[];
  filled: number;
}

export function gridTable(card: Card, value: unknown): GridTable {
  const cfg = card.block_config ?? { rows: [], columns: [] };
  const cells =
    (value as { cells?: Record<string, Record<string, string>> } | null)?.cells ?? {};
  let filled = 0;
  const rows = cfg.rows.map((r) => ({
    label: r.label,
    cells: cfg.columns.map((c) => {
      const v = cells[r.key]?.[c.key] ?? "";
      if (v) filled += 1;
      return v;
    }),
  }));
  return { columns: cfg.columns.map((c) => c.label), rows, filled };
}

function cell(s: string): string {
  return s.replace(/\|/g, "\\|").replace(/\n/g, " ");
}

export function gridMarkdown(t: GridTable): string {
  if (t.filled === 0) return "_Nothing filled in yet._";
  const head = `| | ${t.columns.map(cell).join(" | ")} |`;
  const sep = `|---|${t.columns.map(() => "---").join("|")}|`;
  const body = t.rows.map(
    (r) => `| **${cell(r.label)}** | ${r.cells.map((c) => cell(c) || "—").join(" | ")} |`,
  );
  return [head, sep, ...body].join("\n");
}
