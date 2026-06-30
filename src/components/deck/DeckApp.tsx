import { useEffect, useReducer, useRef, useState } from "react";

import {
  clientApi,
  type Card as CardModel,
  type ClientResponse,
  type Engagement,
} from "@/lib/api";
import { applyBranding } from "@/lib/branding";

import { CardPicker } from "./CardPicker";
import { CardView } from "./CardView";
import { deckReducer, initialDeckState } from "./deck-reducer";
import { encodeResponse, type PendingAction } from "./encode-response";
import type { DeckHandlers } from "./handlers";
import { CompleteCard, DeckError, DeckLoading } from "./states";

interface ReadyBoot {
  engagement: Engagement;
  cards: CardModel[];
  responses: ClientResponse[];
  bootIndex: number;
  orgLogoSrc: string | null;
}

type BootState =
  | { status: "loading" }
  | { status: "error"; title: string; body: string }
  | { status: "ready"; token: string; data: ReadyBoot };

/** First card not yet answered/skipped, or `cards.length` (complete). */
function computeBootIndex(
  cards: CardModel[],
  responses: ClientResponse[],
): number {
  const byId = new Map(responses.map((r) => [r.card_id, r]));
  for (let i = 0; i < cards.length; i++) {
    const r = byId.get(cards[i].id);
    if (!r || (r.state !== "answered" && r.state !== "skipped")) return i;
  }
  return cards.length;
}

export default function DeckApp(): React.ReactElement {
  const [boot, setBoot] = useState<BootState>({ status: "loading" });

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("t");
    if (!token) {
      setBoot({
        status: "error",
        title: "This link is missing a code",
        body: "Please check the link your consultant sent you.",
      });
      return;
    }
    let cancelled = false;
    let logoUrl: string | null = null;
    (async () => {
      try {
        const engagement = await clientApi.me(token);
        const [cards, responses] = await Promise.all([
          clientApi.cards(token),
          clientApi.responses(token),
        ]);
        if (cancelled) return;
        applyBranding(engagement.org_branding);
        logoUrl = await clientApi.logoObjectUrl(token);
        if (cancelled) return;
        setBoot({
          status: "ready",
          token,
          data: {
            engagement,
            cards,
            responses,
            bootIndex: computeBootIndex(cards, responses),
            orgLogoSrc: logoUrl,
          },
        });
      } catch {
        if (!cancelled) {
          setBoot({
            status: "error",
            title: "We could not find your engagement",
            body: "Please check the link or contact your consultant.",
          });
        }
      }
    })();
    return () => {
      cancelled = true;
      if (logoUrl) URL.revokeObjectURL(logoUrl);
    };
  }, []);

  if (boot.status === "loading") return <DeckLoading />;
  if (boot.status === "error") {
    return <DeckError title={boot.title} body={boot.body} />;
  }
  return <DeckRunner token={boot.token} boot={boot.data} />;
}

function DeckRunner({
  token,
  boot,
}: {
  token: string;
  boot: ReadyBoot;
}): React.ReactElement {
  const { cards, engagement } = boot;
  const total = cards.length;

  const [ui, dispatch] = useReducer(deckReducer, undefined, () =>
    initialDeckState(boot.bootIndex, total),
  );
  const [responses, setResponses] = useState<Map<string, ClientResponse>>(
    () => new Map(boot.responses.map((r) => [r.card_id, r])),
  );

  const pendingRef = useRef<PendingAction | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const indexRef = useRef(ui.index);
  indexRef.current = ui.index;

  function clearRetry(): void {
    if (retryRef.current) {
      clearTimeout(retryRef.current);
      retryRef.current = null;
    }
    pendingRef.current = null;
  }

  // Clear any pending retry on unmount and whenever the card changes (mirrors
  // navigateTo() cancelling the retry timer in app.ts).
  useEffect(() => () => clearRetry(), []);
  useEffect(() => {
    clearRetry();
  }, [ui.index]);

  const card = ui.index < total ? cards[ui.index] : null;
  const cardId = card?.id;

  // Mark a card viewed on first view (idempotent server-side; best-effort).
  useEffect(() => {
    if (!cardId || responses.has(cardId)) return;
    let active = true;
    clientApi
      .markViewed(token, cardId)
      .then((r) => {
        if (active) setResponses((m) => new Map(m).set(cardId, r));
      })
      .catch(() => {});
    return () => {
      active = false;
    };
    // Only re-run when the card changes; reading responses at run time is fine.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cardId]);

  async function performSave(action: PendingAction): Promise<void> {
    const startIndex = indexRef.current;
    const current = startIndex < total ? cards[startIndex] : null;
    if (!current) return;

    clearRetry();
    pendingRef.current = action;
    dispatch({ type: "saveStart" });

    const { state, response_value } = encodeResponse(action);

    let saved: ClientResponse;
    try {
      saved = await clientApi.saveResponse(token, {
        card_id: current.id,
        state,
        response_value,
      });
    } catch {
      dispatch({
        type: "saveFailed",
        message: "Could not save just now. We will retry automatically.",
      });
      retryRef.current = setTimeout(() => {
        retryRef.current = null;
        const again = pendingRef.current;
        if (again) void performSave(again);
      }, 10_000);
      return;
    }

    clearRetry();
    setResponses((m) => new Map(m).set(current.id, saved));
    clientApi.heartbeat(token).catch(() => {});
    // Only advance if the recipient is still on the card we saved.
    if (indexRef.current === startIndex) dispatch({ type: "advance" });
  }

  const handlers: DeckHandlers = {
    onConfirm: () => void performSave({ kind: "confirm" }),
    onEditStart: () => dispatch({ type: "editStart" }),
    onEditCancel: () => dispatch({ type: "editCancel" }),
    onEditSubmit: (correction) => void performSave({ kind: "edit", correction }),
    onSingleSelect: (option, note) =>
      void performSave({ kind: "single-select", option, note }),
    onMultiSelectSubmit: (options, note) =>
      void performSave({ kind: "multi-select", options, note }),
    onTextSubmit: (text, note) =>
      void performSave({ kind: "text", text, note }),
    onLinkSubmit: (url, note) => void performSave({ kind: "link", url, note }),
    onContactSubmit: (contact, note) =>
      void performSave({ kind: "contact", ...contact, note }),
    onSkip: (note) => void performSave({ kind: "skip", note }),
    onRetry: () => {
      const p = pendingRef.current;
      if (p) void performSave(p);
    },
    onNavBack: () =>
      dispatch({ type: "navigate", index: indexRef.current - 1 }),
    onNavForward: () =>
      dispatch({ type: "navigate", index: indexRef.current + 1 }),
    onNavJumpTo: (index) => dispatch({ type: "navigate", index }),
    onPickerOpen: () => dispatch({ type: "openPicker" }),
    onPickerClose: () => dispatch({ type: "closePicker" }),
  };

  if (!card) return <CompleteCard name={engagement.recipient_name} />;

  return (
    <>
      <CardView
        card={card}
        position={ui.index + 1}
        total={total}
        mode={ui.mode}
        saveError={ui.saveError}
        showResume={ui.showResume}
        existing={responses.get(card.id)}
        orgLogoSrc={boot.orgLogoSrc}
        orgName={engagement.name}
        handlers={handlers}
      />
      {ui.pickerOpen ? (
        <CardPicker
          cards={cards}
          responses={responses}
          currentIndex={ui.index}
          onJump={handlers.onNavJumpTo}
          onClose={handlers.onPickerClose}
        />
      ) : null}
    </>
  );
}
