import { Check, Minus } from "lucide-react";

import type { Card as CardModel, ClientResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Everything the desktop outline needs to show where the respondent is and
 * let them jump around. Built by the deck runner (live or preview). */
export interface DeckOutlineData {
  cards: CardModel[];
  responses: Map<string, ClientResponse>;
  currentIndex: number;
  onJump: (index: number) => void;
}

type Status = "done" | "skipped" | "current" | "todo";

function statusFor(
  i: number,
  current: number,
  resp?: ClientResponse,
): Status {
  if (i === current) return "current";
  if (resp?.state === "answered") return "done";
  if (resp?.state === "skipped") return "skipped";
  return "todo";
}

/** Consecutive cards sharing a category become one section. */
function sections(cards: CardModel[]): { title: string; items: number[] }[] {
  const out: { title: string; items: number[] }[] = [];
  cards.forEach((c, i) => {
    const title = c.category?.trim() || "Questions";
    const last = out[out.length - 1];
    if (last && last.title === title) last.items.push(i);
    else out.push({ title, items: [i] });
  });
  return out;
}

/** Desktop sidebar: every question grouped by section with done / current /
 * to-do marks, clickable to jump. Replaces the phone's bottom card picker. */
export function DeckOutline({
  cards,
  responses,
  currentIndex,
  onJump,
}: DeckOutlineData): React.ReactElement {
  const answered = cards.filter((c) => {
    const s = responses.get(c.id)?.state;
    return s === "answered" || s === "skipped";
  }).length;
  const pct = cards.length ? Math.round((answered / cards.length) * 100) : 0;

  return (
    <nav aria-label="Questions" className="flex h-full flex-col">
      <div className="flex-1 overflow-y-auto px-3 py-6">
        {sections(cards).map((sec) => (
          <div key={`${sec.title}-${sec.items[0]}`} className="mb-5">
            <h2 className="mb-1.5 px-2 text-xs font-medium text-muted-foreground">
              {sec.title}
            </h2>
            <ol>
              {sec.items.map((i) => {
                const c = cards[i];
                const st = statusFor(i, currentIndex, responses.get(c.id));
                return (
                  <li key={c.id}>
                    <button
                      type="button"
                      onClick={() => onJump(i)}
                      aria-current={st === "current" ? "step" : undefined}
                      className={cn(
                        "flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                        st === "current"
                          ? "bg-muted font-medium text-foreground"
                          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                      )}
                    >
                      <span
                        aria-hidden="true"
                        className={cn(
                          "flex size-4 shrink-0 items-center justify-center rounded-full border [&_svg]:size-2.5",
                          st === "done" && "border-primary bg-primary text-primary-foreground",
                          st === "skipped" && "border-border bg-muted text-muted-foreground",
                          st === "current" && "border-primary",
                          st === "todo" && "border-border",
                        )}
                      >
                        {st === "done" ? <Check strokeWidth={3} /> : null}
                        {st === "skipped" ? <Minus strokeWidth={3} /> : null}
                        {st === "current" ? (
                          <span className="size-1.5 rounded-full bg-primary" />
                        ) : null}
                      </span>
                      <span className="min-w-0 truncate">{c.title}</span>
                      <span className="sr-only">
                        {st === "done"
                          ? "(answered)"
                          : st === "skipped"
                            ? "(skipped)"
                            : st === "current"
                              ? "(current question)"
                              : ""}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ol>
          </div>
        ))}
      </div>
      <div className="border-t border-border px-5 py-4">
        <div className="mb-1.5 flex justify-between text-xs text-muted-foreground">
          <span>
            {answered} of {cards.length} answered
          </span>
          <span className="tabular-nums">{pct}%</span>
        </div>
        <div className="h-1 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full rounded-full bg-primary transition-[width] duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    </nav>
  );
}
