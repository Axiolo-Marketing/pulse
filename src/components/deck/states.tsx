import { CircleCheck, LoaderCircle, TriangleAlert } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

function firstName(name?: string | null): string {
  const trimmed = (name ?? "").trim();
  if (!trimmed) return "there";
  return trimmed.split(/\s+/)[0];
}

export function DeckLoading(): React.ReactElement {
  return (
    <main className="flex min-h-dvh items-center justify-center p-5">
      <div className="flex flex-col items-center gap-3 text-muted-foreground">
        <LoaderCircle className="size-7 animate-spin" aria-hidden="true" />
        <p className="text-sm">Loading…</p>
      </div>
    </main>
  );
}

export function DeckError({
  title,
  body,
}: {
  title: string;
  body: string;
}): React.ReactElement {
  return (
    <main className="flex min-h-dvh items-center justify-center p-5">
      <Card className="w-full max-w-md">
        <CardHeader className="items-center gap-4 p-8 text-center sm:p-10">
          <span className="flex size-12 items-center justify-center rounded-full bg-warning-soft text-warning [&_svg]:size-6">
            <TriangleAlert aria-hidden="true" />
          </span>
          <CardTitle>{title}</CardTitle>
          <CardDescription className="text-balance text-[0.95rem] leading-relaxed">
            {body}
          </CardDescription>
        </CardHeader>
      </Card>
    </main>
  );
}

export function CompleteCard({
  name,
  onReview,
}: {
  name?: string | null;
  onReview?: () => void;
}): React.ReactElement {
  return (
    <main className="flex min-h-dvh items-center justify-center p-5">
      <Card className="w-full max-w-md" data-testid="deck-complete">
        <CardHeader className="items-center gap-4 p-8 text-center sm:p-10">
          <span className="flex size-12 items-center justify-center rounded-full bg-success-soft text-success [&_svg]:size-6">
            <CircleCheck aria-hidden="true" />
          </span>
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Thank you
          </p>
          <CardTitle className="text-2xl">All done, {firstName(name)}</CardTitle>
          <CardContent className="p-0">
            <CardDescription className="text-balance text-[0.95rem] leading-relaxed">
              Your responses are saved. Your consultant will follow up directly.
            </CardDescription>
            {onReview ? (
              <p className="mt-5 text-xs text-muted-foreground">
                Need to change something?{" "}
                <button
                  type="button"
                  onClick={onReview}
                  className="font-medium underline underline-offset-2 hover:text-foreground"
                >
                  Review or edit your answers
                </button>
              </p>
            ) : null}
          </CardContent>
        </CardHeader>
      </Card>
    </main>
  );
}
