import { useEffect, useRef, useState } from "react";
import {
  Ban,
  CircleCheck,
  Clock,
  Link2Off,
  LoaderCircle,
  TriangleAlert,
} from "lucide-react";

import {
  ApiError,
  invitesApi,
  type InviteMetadata,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const BASE_URL = (import.meta.env.BASE_URL ?? "/") as string;

function adminBaseHref(): string {
  return BASE_URL.endsWith("/") ? `${BASE_URL}admin/` : `${BASE_URL}/admin/`;
}

type TerminalKind =
  | "no_token"
  | "expired"
  | "accepted"
  | "revoked"
  | "not_found"
  | "error";

type Phase =
  | { kind: "loading" }
  | { kind: "pending"; meta: InviteMetadata; token: string }
  | {
      kind: "terminal";
      terminal: TerminalKind;
      meta?: InviteMetadata;
      detail?: string;
    };

interface TerminalView {
  icon: React.ReactNode;
  iconClass: string;
  title: string;
  body: (meta?: InviteMetadata, detail?: string) => string;
  /** "accepted" gets a primary "Sign in" CTA; everything else a quiet one. */
  primaryCta?: boolean;
  ctaLabel: string;
}

const TERMINALS: Record<TerminalKind, TerminalView> = {
  no_token: {
    icon: <TriangleAlert aria-hidden="true" />,
    iconClass: "bg-warning-soft text-warning",
    title: "No invite token",
    body: () =>
      "This link doesn't look right. Ask your teammate to resend the invite email and try again.",
    ctaLabel: "Go to sign in",
  },
  expired: {
    icon: <Clock aria-hidden="true" />,
    iconClass: "bg-muted text-muted-foreground",
    title: "This invite has expired",
    body: (meta) =>
      meta
        ? `Ask an owner at ${meta.org_name} to resend it.`
        : "Ask the person who invited you to resend the link.",
    ctaLabel: "Go to sign in",
  },
  accepted: {
    icon: <CircleCheck aria-hidden="true" />,
    iconClass: "bg-success-soft text-success",
    title: "Invite already used",
    body: (meta) =>
      meta
        ? `This invite to ${meta.org_name} was already accepted. Sign in to continue.`
        : "This invite was already accepted. Sign in to continue.",
    primaryCta: true,
    ctaLabel: "Sign in",
  },
  revoked: {
    icon: <Ban aria-hidden="true" />,
    iconClass: "bg-muted text-muted-foreground",
    title: "This invite was revoked",
    body: () => "Ask the person who invited you for a new link.",
    ctaLabel: "Go to sign in",
  },
  not_found: {
    icon: <Link2Off aria-hidden="true" />,
    iconClass: "bg-muted text-muted-foreground",
    title: "Invite not found",
    body: () =>
      "The link is invalid or has been removed. Ask your teammate to resend the invite email.",
    ctaLabel: "Go to sign in",
  },
  error: {
    icon: <TriangleAlert aria-hidden="true" />,
    iconClass: "bg-warning-soft text-warning",
    title: "Could not load invite",
    body: (_meta, detail) =>
      detail ?? "Something went wrong on our end. Please try again.",
    ctaLabel: "Go to sign in",
  },
};

function CardShell({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <main className="flex flex-1 items-center justify-center p-5">
      <Card className="w-full max-w-md" data-testid="invite-card">
        {children}
      </Card>
    </main>
  );
}

function TerminalCard({
  terminal,
  meta,
  detail,
}: {
  terminal: TerminalKind;
  meta?: InviteMetadata;
  detail?: string;
}): React.ReactElement {
  const v = TERMINALS[terminal];
  return (
    <CardShell>
      <CardHeader className="items-center gap-4 p-8 text-center sm:p-10">
        <span
          className={`flex size-12 items-center justify-center rounded-full [&_svg]:size-6 ${v.iconClass}`}
        >
          {v.icon}
        </span>
        <CardTitle data-testid="invite-title">{v.title}</CardTitle>
        <CardDescription className="text-balance text-[0.95rem] leading-relaxed">
          {v.body(meta, detail)}
        </CardDescription>
        <Button
          asChild
          variant={v.primaryCta ? "default" : "outline"}
          className="mt-2 w-full"
        >
          <a href={adminBaseHref()}>{v.ctaLabel}</a>
        </Button>
      </CardHeader>
    </CardShell>
  );
}

function PendingCard({
  token,
  meta,
  onConcurrentAccept,
}: {
  token: string;
  meta: InviteMetadata;
  onConcurrentAccept: () => void;
}): React.ReactElement {
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [oauthBusy, setOauthBusy] = useState<"google" | "microsoft" | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const pwRef = useRef<HTMLInputElement>(null);

  const roleLabel = meta.role === "owner" ? "Owner" : "Member";
  const busy = submitting || oauthBusy !== null;

  async function startOAuth(provider: "google" | "microsoft"): Promise<void> {
    setError(null);
    setOauthBusy(provider);
    try {
      const { redirect_url } = await invitesApi.acceptWithOAuth(token, provider);
      window.location.assign(redirect_url);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Could not start sign-in",
      );
      setOauthBusy(null);
    }
  }

  async function submitPassword(): Promise<void> {
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      pwRef.current?.focus();
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await invitesApi.acceptWithPassword(token, {
        password,
        name: name.trim() || null,
      });
      // Backend set the session cookie; land on the admin home.
      window.location.assign(adminBaseHref());
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setError(
          "An account already exists for this email. Sign in normally — your invite will be attached after.",
        );
      } else if (err instanceof ApiError && err.status === 410) {
        onConcurrentAccept();
        return;
      } else {
        setError(
          err instanceof ApiError ? err.detail : "Could not accept invite",
        );
      }
      setSubmitting(false);
    }
  }

  return (
    <CardShell>
      <CardHeader className="gap-1.5 p-7 sm:p-8">
        <p className="text-xs font-semibold uppercase tracking-wide text-primary">
          You're invited
        </p>
        <CardTitle className="text-xl" data-testid="invite-title">
          Join {meta.org_name}
        </CardTitle>
        <CardDescription>
          Invitation for <strong className="font-semibold">{meta.email}</strong>{" "}
          · Role: <strong className="font-semibold">{roleLabel}</strong>
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 p-7 pt-0 sm:p-8 sm:pt-0">
        <div className="flex flex-col gap-2">
          <Button
            variant="outline"
            type="button"
            disabled={busy}
            onClick={() => startOAuth("google")}
          >
            {oauthBusy === "google" && (
              <LoaderCircle className="animate-spin" aria-hidden="true" />
            )}
            Continue with Google
          </Button>
          <Button
            variant="outline"
            type="button"
            disabled={busy}
            onClick={() => startOAuth("microsoft")}
          >
            {oauthBusy === "microsoft" && (
              <LoaderCircle className="animate-spin" aria-hidden="true" />
            )}
            Continue with Microsoft
          </Button>
        </div>

        <div className="flex items-center gap-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          <span className="h-px flex-1 bg-border" />
          or set a password
          <span className="h-px flex-1 bg-border" />
        </div>

        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void submitPassword();
          }}
          noValidate
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="invite-name">Your name (optional)</Label>
            <Input
              id="invite-name"
              type="text"
              autoComplete="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="invite-pw">Password (8+ characters)</Label>
            <Input
              id="invite-pw"
              ref={pwRef}
              type="password"
              autoComplete="new-password"
              minLength={8}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
            />
          </div>

          {/* The live region is always rendered (and never display:none) so it
              exists in the DOM before it's populated — VoiceOver won't announce
              a node inserted with content already set. The margin only applies
              when it has content, so there's no empty gap above the button. */}
          <div className="flex flex-col">
            <p
              className="text-sm font-medium text-destructive [&:not(:empty)]:mb-3"
              role="status"
              aria-live="polite"
              aria-atomic="true"
              data-testid="invite-error"
            >
              {error ?? ""}
            </p>
            <Button type="submit" disabled={busy}>
              {submitting && (
                <LoaderCircle className="animate-spin" aria-hidden="true" />
              )}
              {submitting ? "Accepting…" : "Accept and sign in"}
            </Button>
          </div>
        </form>

        <p className="text-xs leading-relaxed text-muted-foreground">
          Already have a Pulse account at this email? Sign in normally — the
          invite will be attached automatically.
        </p>
      </CardContent>
    </CardShell>
  );
}

export default function InviteApp(): React.ReactElement {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token");
    if (!token) {
      setPhase({ kind: "terminal", terminal: "no_token" });
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const meta = await invitesApi.resolve(token);
        if (cancelled) return;
        if (meta.status === "pending") {
          setPhase({ kind: "pending", meta, token });
        } else {
          // expired | accepted | revoked all map to a terminal of the same name.
          setPhase({ kind: "terminal", terminal: meta.status, meta });
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setPhase({ kind: "terminal", terminal: "not_found" });
        } else {
          setPhase({
            kind: "terminal",
            terminal: "error",
            detail: err instanceof ApiError ? err.detail : undefined,
          });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (phase.kind === "loading") {
    return (
      <CardShell>
        <CardContent className="flex flex-col items-center gap-3 p-10 text-center">
          <LoaderCircle
            className="size-7 animate-spin text-muted-foreground"
            aria-hidden="true"
          />
          <p className="text-sm text-muted-foreground">Loading invite…</p>
        </CardContent>
      </CardShell>
    );
  }

  if (phase.kind === "pending") {
    return (
      <PendingCard
        token={phase.token}
        meta={phase.meta}
        onConcurrentAccept={() =>
          setPhase({ kind: "terminal", terminal: "accepted", meta: phase.meta })
        }
      />
    );
  }

  return (
    <TerminalCard
      terminal={phase.terminal}
      meta={phase.meta}
      detail={phase.detail}
    />
  );
}
