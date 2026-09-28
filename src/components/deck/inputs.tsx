import { useEffect, useState } from "react";
import { Check } from "lucide-react";

import type { Card as CardModel, ClientResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FINE_POINTER_QUERY, useMediaQuery } from "@/lib/use-media-query";
import { cn } from "@/lib/utils";

import { NoteField } from "./chrome";
import { VOICE_PLACEHOLDER } from "./constants";

// ── shared helpers ──────────────────────────────────────────────────────────

/** Number keys 1–9 pick the matching option on devices with a keyboard.
 * Ignored while typing in a field (e.g. the notes box) or with a modifier.
 * Returns whether hints should be shown (mouse + keyboard present). */
function useOptionKeys(
  count: number,
  onPick: (index: number) => void,
  disabled: boolean,
): boolean {
  const hasKeyboard = useMediaQuery(FINE_POINTER_QUERY);
  useEffect(() => {
    if (!hasKeyboard || disabled) return;
    function onKey(e: KeyboardEvent): void {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target;
      if (
        t instanceof HTMLElement &&
        (t.isContentEditable ||
          t.closest('[role="dialog"]') ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName))
      ) {
        return;
      }
      const n = Number.parseInt(e.key, 10);
      if (Number.isInteger(n) && n >= 1 && n <= Math.min(count, 9)) {
        e.preventDefault();
        onPick(n - 1);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasKeyboard, disabled, count, onPick]);
  return hasKeyboard;
}

function OptionKey({ n }: { n: number }): React.ReactElement | null {
  if (n > 9) return null;
  return (
    <kbd
      aria-hidden="true"
      className="ml-auto inline-flex h-5 min-w-5 items-center justify-center rounded border border-border bg-muted px-1 font-sans text-[11px] font-medium text-muted-foreground"
    >
      {n}
    </kbd>
  );
}

function priorValue(existing?: ClientResponse): Record<string, unknown> {
  return (existing?.response_value ?? {}) as Record<string, unknown>;
}

function isValidUrl(raw: string): boolean {
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

function SkipButton({
  card,
  saving,
  onSkip,
}: {
  card: CardModel;
  saving: boolean;
  onSkip: () => void;
}): React.ReactElement | null {
  if (!card.skip_allowed) return null;
  return (
    <Button
      variant="ghost"
      type="button"
      disabled={saving}
      onClick={onSkip}
      className="text-muted-foreground"
    >
      Skip for now
    </Button>
  );
}

/** Vertical actions row used at the bottom of every input body. */
function Actions({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  // h-11: comfortable thumb targets on a phone.
  return (
    <div className="mt-6 flex flex-col gap-2 [&>button]:h-11 [&>button]:text-[0.95rem]">
      {children}
    </div>
  );
}

// ── confirm-edit (view) ─────────────────────────────────────────────────────

export function ConfirmEditView({
  card,
  saving,
  onConfirm,
  onEditStart,
  onSkip,
}: {
  card: CardModel;
  saving: boolean;
  onConfirm: () => void;
  onEditStart: () => void;
  onSkip: () => void;
}): React.ReactElement {
  return (
    <>
      {card.default_value ? (
        <div className="rounded-lg border border-border bg-muted/50 px-4 py-3">
          <p className="text-xs text-muted-foreground">What we have</p>
          <p className="mt-1 whitespace-pre-wrap text-[0.95rem] font-medium text-foreground">
            {card.default_value}
          </p>
        </div>
      ) : null}
      <Actions>
        <Button type="button" disabled={saving} onClick={onConfirm}>
          {saving ? "Saving…" : "Yes, correct"}
        </Button>
        <Button
          variant="outline"
          type="button"
          disabled={saving}
          onClick={onEditStart}
        >
          Needs edit
        </Button>
        <SkipButton card={card} saving={saving} onSkip={onSkip} />
      </Actions>
    </>
  );
}

// ── confirm-edit (edit) ─────────────────────────────────────────────────────

export function EditBody({
  card,
  saving,
  existing,
  onSubmit,
  onCancel,
}: {
  card: CardModel;
  saving: boolean;
  existing?: ClientResponse;
  onSubmit: (correction: string) => void;
  onCancel: () => void;
}): React.ReactElement {
  const prior = priorValue(existing);
  const initial =
    typeof prior.correction === "string"
      ? prior.correction
      : (card.default_value ?? "");
  const [text, setText] = useState(initial);
  const valid = text.trim().length > 0;
  return (
    <>
      <Textarea
        autoFocus
        rows={5}
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={saving}
        placeholder="What should we update? A short note is fine."
      />
      <Actions>
        <Button
          type="button"
          disabled={saving || !valid}
          onClick={() => onSubmit(text.trim())}
        >
          {saving ? "Saving…" : "Save changes"}
        </Button>
        <Button
          variant="ghost"
          type="button"
          disabled={saving}
          onClick={onCancel}
          className="text-muted-foreground"
        >
          Cancel
        </Button>
      </Actions>
    </>
  );
}

// ── single-select ───────────────────────────────────────────────────────────

export function SingleSelectInput({
  card,
  saving,
  existing,
  onSelect,
  onNoteSubmit,
  onSkip,
}: {
  card: CardModel;
  saving: boolean;
  existing?: ClientResponse;
  onSelect: (option: string, note?: string) => void;
  onNoteSubmit: (note: string, option?: string) => void;
  onSkip: (note?: string) => void;
}): React.ReactElement {
  const prior = priorValue(existing);
  const [note, setNote] = useState(
    typeof prior.note === "string" ? prior.note : "",
  );
  const selected = typeof prior.selected === "string" ? prior.selected : null;
  const options = card.options ?? [];
  const showKeys = useOptionKeys(
    options.length,
    (i) => onSelect(options[i], note.trim() || undefined),
    saving,
  );
  return (
    <>
      <div
        className="flex flex-col gap-2"
        role="radiogroup"
        aria-label={card.question}
      >
        {options.map((option, idx) => {
          const isSel = option === selected;
          return (
            <button
              key={option}
              type="button"
              role="radio"
              aria-checked={isSel}
              disabled={saving}
              onClick={() => onSelect(option, note.trim() || undefined)}
              className={cn(
                "flex min-h-12 items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 text-left text-[0.95rem] transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60",
                isSel
                  ? "border-primary font-medium text-foreground ring-1 ring-primary"
                  : "text-foreground hover:border-foreground/30 hover:bg-muted/40",
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "flex size-5 shrink-0 items-center justify-center rounded-full border-[1.5px] border-border",
                  isSel && "border-primary",
                )}
              >
                {isSel ? <span className="size-2.5 rounded-full bg-primary" /> : null}
              </span>
              {option}
              {showKeys ? <OptionKey n={idx + 1} /> : null}
            </button>
          );
        })}
      </div>
      <NoteField value={note} onChange={setNote} disabled={saving} />
      <Actions>
        {/* Options auto-save on tap; this is the note's own submit path —
            for a respondent whose answer is the free-form note itself, or
            who typed one after tapping an option. */}
        <Button
          type="button"
          disabled={saving || note.trim() === ""}
          onClick={() => onNoteSubmit(note.trim(), selected ?? undefined)}
        >
          {saving ? "Saving…" : "Send note"}
        </Button>
        <SkipButton
          card={card}
          saving={saving}
          onSkip={() => onSkip(note.trim() || undefined)}
        />
      </Actions>
    </>
  );
}

// ── multi-select ────────────────────────────────────────────────────────────

export function MultiSelectInput({
  card,
  saving,
  existing,
  onSubmit,
  onSkip,
}: {
  card: CardModel;
  saving: boolean;
  existing?: ClientResponse;
  onSubmit: (options: string[], note?: string) => void;
  onSkip: (note?: string) => void;
}): React.ReactElement {
  const prior = priorValue(existing);
  const [note, setNote] = useState(
    typeof prior.note === "string" ? prior.note : "",
  );
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(Array.isArray(prior.selected) ? (prior.selected as string[]) : []),
  );
  const options = card.options ?? [];
  const showKeys = useOptionKeys(
    options.length,
    (i) => toggle(options[i]),
    saving,
  );

  function toggle(option: string): void {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(option)) next.delete(option);
      else next.add(option);
      return next;
    });
  }

  return (
    <>
      <div
        className="flex flex-col gap-2"
        role="group"
        aria-label={card.question}
      >
        {options.map((option, idx) => {
          const isSel = selected.has(option);
          return (
            <button
              key={option}
              type="button"
              role="checkbox"
              aria-checked={isSel}
              disabled={saving}
              onClick={() => toggle(option)}
              className={cn(
                "flex min-h-12 items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 text-left text-[0.95rem] transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-60",
                isSel
                  ? "border-primary font-medium text-foreground ring-1 ring-primary"
                  : "text-foreground hover:border-foreground/30 hover:bg-muted/40",
              )}
            >
              <span
                className={cn(
                  "flex size-5 shrink-0 items-center justify-center rounded-[5px] border-[1.5px] border-border [&_svg]:size-3.5",
                  isSel && "border-primary bg-primary text-primary-foreground",
                )}
              >
                {isSel ? <Check aria-hidden="true" /> : null}
              </span>
              {option}
              {showKeys ? <OptionKey n={idx + 1} /> : null}
            </button>
          );
        })}
      </div>
      <NoteField value={note} onChange={setNote} disabled={saving} />
      <Actions>
        <Button
          type="button"
          disabled={saving}
          onClick={() =>
            onSubmit(Array.from(selected), note.trim() || undefined)
          }
        >
          {saving ? "Saving…" : "Continue"}
        </Button>
        <SkipButton
          card={card}
          saving={saving}
          onSkip={() => onSkip(note.trim() || undefined)}
        />
      </Actions>
    </>
  );
}

// ── short-text / long-text ──────────────────────────────────────────────────

export function TextInput({
  card,
  saving,
  existing,
  multiline,
  onSubmit,
  onSkip,
}: {
  card: CardModel;
  saving: boolean;
  existing?: ClientResponse;
  multiline: boolean;
  onSubmit: (text: string) => void;
  onSkip: () => void;
}): React.ReactElement {
  const prior = priorValue(existing);
  const [text, setText] = useState(
    typeof prior.text === "string" ? prior.text : "",
  );
  const valid = text.trim().length > 0;
  return (
    <>
      {multiline ? (
        <Textarea
          rows={5}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={saving}
          placeholder={VOICE_PLACEHOLDER}
        />
      ) : (
        <Input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={saving}
          placeholder={VOICE_PLACEHOLDER}
        />
      )}
      <Actions>
        <Button
          type="button"
          disabled={saving || !valid}
          onClick={() => onSubmit(text.trim())}
        >
          {saving ? "Saving…" : "Submit"}
        </Button>
        <SkipButton card={card} saving={saving} onSkip={onSkip} />
      </Actions>
    </>
  );
}

// ── document-link ───────────────────────────────────────────────────────────

export function DocumentLinkInput({
  card,
  saving,
  existing,
  onSubmit,
  onSkip,
}: {
  card: CardModel;
  saving: boolean;
  existing?: ClientResponse;
  onSubmit: (url: string, note?: string) => void;
  onSkip: (note?: string) => void;
}): React.ReactElement {
  const prior = priorValue(existing);
  const [url, setUrl] = useState(typeof prior.url === "string" ? prior.url : "");
  const [note, setNote] = useState(
    typeof prior.note === "string" ? prior.note : "",
  );
  const [touched, setTouched] = useState(false);
  const valid = isValidUrl(url.trim());
  return (
    <>
      <Input
        type="url"
        inputMode="url"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        onBlur={() => setTouched(true)}
        disabled={saving}
        placeholder="https://…"
        aria-invalid={touched && url.trim() !== "" && !valid}
      />
      {touched && url.trim() !== "" && !valid ? (
        <p className="mt-1.5 text-sm text-destructive">
          Enter a valid http(s) link.
        </p>
      ) : null}
      <NoteField value={note} onChange={setNote} disabled={saving} />
      <Actions>
        <Button
          type="button"
          disabled={saving || !valid}
          onClick={() => onSubmit(url.trim(), note.trim() || undefined)}
        >
          {saving ? "Saving…" : "Submit"}
        </Button>
        <SkipButton
          card={card}
          saving={saving}
          onSkip={() => onSkip(note.trim() || undefined)}
        />
      </Actions>
    </>
  );
}

// ── contact-share ───────────────────────────────────────────────────────────

export function ContactShareInput({
  card,
  saving,
  existing,
  onSubmit,
  onSkip,
}: {
  card: CardModel;
  saving: boolean;
  existing?: ClientResponse;
  onSubmit: (
    contact: { name: string; email: string; role: string },
    note?: string,
  ) => void;
  onSkip: (note?: string) => void;
}): React.ReactElement {
  const prior = priorValue(existing);
  const [name, setName] = useState(
    typeof prior.name === "string" ? prior.name : "",
  );
  const [email, setEmail] = useState(
    typeof prior.email === "string" ? prior.email : "",
  );
  const [role, setRole] = useState(
    typeof prior.role === "string" ? prior.role : "",
  );
  const [note, setNote] = useState(
    typeof prior.note === "string" ? prior.note : "",
  );
  const valid = name.trim() !== "" && email.trim() !== "";
  return (
    <>
      <div className="flex flex-col gap-2.5">
        <Input
          type="text"
          aria-label="Name"
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={saving}
          placeholder="Name"
        />
        <Input
          type="email"
          aria-label="Email"
          inputMode="email"
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={saving}
          placeholder="Email"
        />
        <Input
          type="text"
          value={role}
          onChange={(e) => setRole(e.target.value)}
          disabled={saving}
          aria-label="Role"
          placeholder="Role (optional)"
        />
      </div>
      <NoteField value={note} onChange={setNote} disabled={saving} />
      <Actions>
        <Button
          type="button"
          disabled={saving || !valid}
          onClick={() =>
            onSubmit(
              {
                name: name.trim(),
                email: email.trim(),
                role: role.trim(),
              },
              note.trim() || undefined,
            )
          }
        >
          {saving ? "Saving…" : "Share contact"}
        </Button>
        <SkipButton
          card={card}
          saving={saving}
          onSkip={() => onSkip(note.trim() || undefined)}
        />
      </Actions>
    </>
  );
}

