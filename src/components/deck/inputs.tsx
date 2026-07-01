import { useState } from "react";
import { Check } from "lucide-react";

import type { Card as CardModel, ClientResponse } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { NoteField } from "./chrome";
import { VOICE_PLACEHOLDER } from "./constants";

// ── shared helpers ──────────────────────────────────────────────────────────

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
  return <div className="mt-6 flex flex-col gap-2.5">{children}</div>;
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
        <p className="rounded-lg border border-border bg-muted px-4 py-3 text-[0.95rem] font-medium text-foreground">
          {card.default_value}
        </p>
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
  onSkip,
}: {
  card: CardModel;
  saving: boolean;
  existing?: ClientResponse;
  onSelect: (option: string, note?: string) => void;
  onSkip: (note?: string) => void;
}): React.ReactElement {
  const prior = priorValue(existing);
  const [note, setNote] = useState(
    typeof prior.note === "string" ? prior.note : "",
  );
  const selected = typeof prior.selected === "string" ? prior.selected : null;
  const options = card.options ?? [];
  return (
    <>
      <div
        className="flex flex-col gap-2"
        role="radiogroup"
        aria-label={card.question}
      >
        {options.map((option) => {
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
                "min-h-12 rounded-lg border-[1.5px] border-border bg-card px-4 py-3.5 text-left text-[0.95rem] font-medium transition-colors disabled:opacity-60",
                isSel
                  ? "border-primary bg-secondary font-semibold text-secondary-foreground"
                  : "hover:border-primary/50",
              )}
            >
              {option}
            </button>
          );
        })}
      </div>
      <NoteField value={note} onChange={setNote} disabled={saving} />
      <Actions>
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
        {options.map((option) => {
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
                "flex min-h-12 items-center gap-3 rounded-lg border-[1.5px] border-border bg-card px-4 py-3.5 text-left text-[0.95rem] font-medium transition-colors disabled:opacity-60",
                isSel
                  ? "border-primary bg-secondary font-semibold text-secondary-foreground"
                  : "hover:border-primary/50",
              )}
            >
              <span
                className={cn(
                  "flex size-5 shrink-0 items-center justify-center rounded border-[1.5px] border-border [&_svg]:size-3.5",
                  isSel && "border-primary bg-primary text-primary-foreground",
                )}
              >
                {isSel ? <Check aria-hidden="true" /> : null}
              </span>
              {option}
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
      <div className="flex flex-col gap-3">
        <Input
          type="text"
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={saving}
          placeholder="Name"
        />
        <Input
          type="email"
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
          placeholder="Role"
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

