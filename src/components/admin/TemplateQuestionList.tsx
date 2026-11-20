import type { TemplateCard } from "@/lib/api";
import { responseTypeLabel } from "@/lib/response-types";
import { cn } from "@/lib/utils";

/** Read-only, scrollable list of a template's questions: position, title,
 * response type and a "Required" marker when the respondent can't skip. */
export function TemplateQuestionList({
  cards,
  className,
}: {
  cards: TemplateCard[];
  className?: string;
}): React.ReactElement {
  return (
    <ol
      className={cn(
        "max-h-56 divide-y divide-border overflow-y-auto rounded-lg border border-border",
        className,
      )}
    >
      {cards.map((c, i) => (
        <li key={i} className="flex items-start gap-3 px-3 py-2">
          <span className="flex size-5 shrink-0 items-center justify-center rounded bg-muted text-[11px] font-medium tabular-nums text-muted-foreground">
            {i + 1}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-foreground">
              {c.title}
            </p>
            <p className="text-xs text-muted-foreground">
              {responseTypeLabel(c.response_type)}
              {c.skip_allowed ? "" : " · Required"}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}
