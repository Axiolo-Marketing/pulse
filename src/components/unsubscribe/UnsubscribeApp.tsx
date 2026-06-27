import { useEffect, useState } from "react";
import { CircleCheck, Link2Off, LoaderCircle, TriangleAlert } from "lucide-react";

import { API_BASE } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type Status = "loading" | "done" | "expired" | "invalid" | "error";

interface View {
  icon: React.ReactNode;
  iconClass: string;
  title: string;
  body: string;
}

const VIEWS: Record<Exclude<Status, "loading">, View> = {
  done: {
    icon: <CircleCheck aria-hidden="true" />,
    iconClass: "bg-success-soft text-success",
    title: "You're unsubscribed",
    body: "You won't receive any more reminders about this. You can still open your questions from your original link whenever you're ready.",
  },
  expired: {
    icon: <Link2Off aria-hidden="true" />,
    iconClass: "bg-muted text-muted-foreground",
    title: "Link expired",
    body: "This unsubscribe link is invalid or has expired.",
  },
  invalid: {
    icon: <Link2Off aria-hidden="true" />,
    iconClass: "bg-muted text-muted-foreground",
    title: "Invalid link",
    body: "This unsubscribe link is missing its code.",
  },
  error: {
    icon: <TriangleAlert aria-hidden="true" />,
    iconClass: "bg-warning-soft text-warning",
    title: "Something went wrong",
    body: "Please try again in a moment.",
  },
};

export default function UnsubscribeApp(): React.ReactElement {
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("u");
    if (!token) {
      setStatus("invalid");
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${API_BASE}/api/reminders/unsubscribe`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        });
        if (cancelled) return;
        setStatus(res.ok ? "done" : "expired");
      } catch {
        if (!cancelled) setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="flex min-h-dvh items-center justify-center p-5">
      <Card className="w-full max-w-md" data-testid="unsubscribe-card">
        {status === "loading" ? (
          <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
            <LoaderCircle
              className="size-7 animate-spin text-muted-foreground"
              aria-hidden="true"
            />
            <p className="text-sm text-muted-foreground">Loading…</p>
          </CardContent>
        ) : (
          <CardHeader className="items-center gap-4 p-8 text-center sm:p-10">
            <span
              className={`flex size-12 items-center justify-center rounded-full [&_svg]:size-6 ${VIEWS[status].iconClass}`}
            >
              {VIEWS[status].icon}
            </span>
            <CardTitle data-testid="unsubscribe-title">
              {VIEWS[status].title}
            </CardTitle>
            <CardDescription className="text-balance text-[0.95rem] leading-relaxed">
              {VIEWS[status].body}
            </CardDescription>
          </CardHeader>
        )}
      </Card>
    </main>
  );
}
