import { useEffect } from "react";
import { X } from "lucide-react";

import type { Card as CardModel, ClientResponse } from "@/lib/api";
import { cn } from "@/lib/utils";

function badgeFor(resp?: ClientResponse): { label: string; cls: string } {
  switch (resp?.state) {
    case "answered":
      return { label: "Answered", cls: "bg-secondary text-secondary-foreground" };
    case "skipped":
      return { label: "Skipped", cls: "bg-warning-soft text-warning" };
    case "viewed":
      return { label: "Viewed", cls: "bg-muted text-muted-foreground" };
    default:
      return { label: "Not viewed", cls: "bg-muted text-muted-foreground" };
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
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Jump to card"
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <button
        type="button"
        aria-label="Close"
        tabIndex={-1}
        className="absolute inset-0 cursor-default bg-black/45"
        onClick={onClose}
      />
      <div className="relative flex max-h-[calc(100dvh-2rem)] w-full max-w-md flex-col overflow-hidden rounded-xl bg-card shadow-lg">
        <header className="flex items-center justify-between bg-foreground px-4 py-3 text-[color:var(--card)]">
          <span className="font-semibold">Jump to card</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex size-8 items-center justify-center rounded-md hover:bg-white/10 [&_svg]:size-5"
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
                  "grid w-full grid-cols-[28px_1fr_auto] items-center gap-2 rounded-md px-2 py-3 text-left text-sm transition-colors hover:bg-muted",
                  i === currentIndex &&
                    "bg-secondary font-semibold text-secondary-foreground",
                )}
              >
                <span className="text-muted-foreground">{i + 1}.</span>
                <span className="truncate">{c.title}</span>
                <span
                  className={cn(
                    "whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold",
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
