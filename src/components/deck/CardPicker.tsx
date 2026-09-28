import { useRef } from "react";
import { X } from "lucide-react";

import type { Card as CardModel, ClientResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

import { useModalA11y } from "./use-modal-a11y";

function badgeFor(resp?: ClientResponse): { label: string; cls: string } {
  switch (resp?.state) {
    case "answered":
      return { label: "Answered", cls: "border-success/20 bg-success-soft text-success" };
    case "skipped":
      return { label: "Skipped", cls: "border-border bg-muted text-muted-foreground" };
    case "viewed":
      return { label: "Viewed", cls: "border-border text-muted-foreground" };
    default:
      return { label: "Not viewed", cls: "border-dashed border-border text-muted-foreground" };
  }
}

export function CardPicker({
  cards,
  responses,
  currentIndex,
  onJump,
  onClose,
}: {
  cards: CardModel[];
  responses: Map<string, ClientResponse>;
  currentIndex: number;
  onJump: (index: number) => void;
  onClose: () => void;
}): React.ReactElement {
  const panelRef = useRef<HTMLDivElement>(null);
  useModalA11y(panelRef, onClose);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Jump to card"
      className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-4"
    >
      <button
        type="button"
        aria-label="Close"
        tabIndex={-1}
        className="absolute inset-0 cursor-default bg-black/40 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        className="relative flex max-h-[80dvh] w-full max-w-md flex-col overflow-hidden rounded-t-2xl border border-border bg-card pb-[env(safe-area-inset-bottom)] shadow-xl outline-none sm:max-h-[calc(100dvh-2rem)] sm:rounded-xl sm:pb-0"
      >
        <header className="flex items-center justify-between border-b border-border px-5 py-3.5">
          <span className="text-base font-semibold text-foreground">Jump to card</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex size-8 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground [&_svg]:size-4"
          >
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto p-2">
          {cards.map((c, i) => {
            const badge = badgeFor(responses.get(c.id));
            return (
              <button
                key={c.id}
                type="button"
                onClick={() => onJump(i)}
                className={cn(
                  "grid w-full grid-cols-[28px_1fr_auto] items-center gap-2 rounded-lg px-3 py-3 text-left text-sm text-foreground transition-colors hover:bg-muted",
                  i === currentIndex && "bg-muted font-medium",
                )}
              >
                <span className="tabular-nums text-muted-foreground">{i + 1}</span>
                <span className="truncate">{c.title}</span>
                <span
                  className={cn(
                    "whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-none",
                    badge.cls,
                  )}
                >
                  {badge.label}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
