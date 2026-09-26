import { useEffect, useState } from "react";
import { Eye, RotateCcw } from "lucide-react";

import {
  adminApi,
  ApiError,
  orgLogoUrl,
  orgsApi,
  type Card as CardModel,
} from "@/lib/api";
import { applyBranding } from "@/lib/branding";

import { PreviewDeck } from "./PreviewDeck";
import { DeckError, DeckLoading } from "./states";

interface Loaded {
  cards: CardModel[];
  voiceEnabled: boolean;
  orgLogoSrc: string | null;
  orgName: string | null;
}

type State =
  | { status: "loading" }
  | { status: "error"; title: string; body: string }
  | { status: "ready"; data: Loaded };

/** Operator preview of an engagement's respondent deck: `/v2/preview?e=<id>`.
 * Loads through the admin session (cookie) — never a respondent token — and
 * applies the org's live branding so it looks exactly like the real deck.
 * Only shared cards are shown (per-respondent AI follow-ups are skipped).
 * Nothing is saved; see `PreviewDeck`. */
export default function PreviewApp(): React.ReactElement {
  const [state, setState] = useState<State>({ status: "loading" });
  const [run, setRun] = useState(0); // bump to restart from card 1
  const embedded = typeof window !== "undefined" && window.self !== window.top;

  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get("e");
    if (!id) {
      setState({
        status: "error",
        title: "Nothing to preview",
        body: "Open the preview from an engagement in the admin.",
      });
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const [detail, org] = await Promise.all([
          adminApi.getEngagement(id),
          orgsApi.me(),
        ]);
        if (cancelled) return;
        applyBranding(org.branding);
        const cards = detail.cards
          .filter((c) => !c.recipient_id)
          .sort((a, b) => a.order_index - b.order_index);
        setState({
          status: "ready",
          data: {
            cards,
            voiceEnabled: detail.engagement.voice_enabled,
            orgLogoSrc: orgLogoUrl(org.logo_path),
            orgName: org.name,
          },
        });
      } catch (err) {
        if (cancelled) return;
        const signedOut = err instanceof ApiError && err.status === 401;
        setState({
          status: "error",
          title: signedOut ? "Sign in to preview" : "Couldn't load this preview",
          body: signedOut
            ? "Previews use your admin sign-in. Sign in to the Pulse admin, then try again."
            : "The engagement may have been deleted, or belongs to another organization.",
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.status === "loading") return <DeckLoading />;
  if (state.status === "error") {
    return <DeckError title={state.title} body={state.body} />;
  }
  if (state.data.cards.length === 0) {
    return (
      <DeckError
        title="No questions yet"
        body="Add or import cards, then preview the deck."
      />
    );
  }

  return (
    <>
      {embedded ? null : (
        <div className="flex items-center justify-center gap-3 bg-foreground px-4 py-2 text-xs text-background">
          <span className="flex items-center gap-1.5">
            <Eye className="size-3.5" aria-hidden="true" />
            Preview. Answers aren't saved.
          </span>
          <button
            type="button"
            onClick={() => setRun((n) => n + 1)}
            className="flex items-center gap-1 underline-offset-2 hover:underline"
          >
            <RotateCcw className="size-3" aria-hidden="true" />
            Restart
          </button>
        </div>
      )}
      <PreviewDeck key={run} {...state.data} />
    </>
  );
}
