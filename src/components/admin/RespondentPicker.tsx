import { useId, useMemo, useRef, useState } from "react";
import { Plus, UserRound, X } from "lucide-react";

import type { ClientContact } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface PickedRespondent {
  email: string;
  name?: string | null;
}

const EMAIL_RE = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/;

export function isEmail(value: string): boolean {
  return EMAIL_RE.test(value.trim());
}

/** Chips input for choosing respondents. Type-ahead suggests the client's
 * saved contacts; typing (or pasting) any email and pressing Enter, comma or
 * Tab adds it as a new person. `exclude` hides emails that are already
 * respondents so they can't be picked twice. */
export function RespondentPicker({
  id,
  contacts,
  value,
  onChange,
  exclude = [],
  disabled,
  autoFocus,
}: {
  id?: string;
  contacts: ClientContact[];
  value: PickedRespondent[];
  onChange: (next: PickedRespondent[]) => void;
  exclude?: string[];
  disabled?: boolean;
  autoFocus?: boolean;
}): React.ReactElement {
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  const taken = useMemo(
    () =>
      new Set([
        ...exclude.map((e) => e.toLowerCase()),
        ...value.map((v) => v.email.toLowerCase()),
      ]),
    [exclude, value],
  );

  const q = query.trim().toLowerCase();
  const suggestions = contacts
    .filter((c) => !taken.has(c.email.toLowerCase()))
    .filter(
      (c) =>
        !q ||
        c.email.toLowerCase().includes(q) ||
        (c.name ?? "").toLowerCase().includes(q),
    )
    .slice(0, 8);
  const canAddTyped =
    isEmail(query) &&
    !taken.has(q) &&
    !contacts.some((c) => c.email.toLowerCase() === q);
  const options: PickedRespondent[] = [
    ...suggestions.map((c) => ({ email: c.email, name: c.name })),
    ...(canAddTyped ? [{ email: query.trim() }] : []),
  ];
  const showList = open && !disabled && options.length > 0;

  function add(items: PickedRespondent[]): void {
    const seen = new Set(taken);
    const fresh: PickedRespondent[] = [];
    for (const it of items) {
      const key = it.email.trim().toLowerCase();
      if (!isEmail(key) || seen.has(key)) continue;
      seen.add(key);
      fresh.push({ email: it.email.trim(), name: it.name ?? null });
    }
    if (fresh.length) onChange([...value, ...fresh]);
    setQuery("");
    setActive(0);
  }

  /** Emails typed or pasted as a list ("a@x.com, b@y.com"). */
  function addTyped(raw: string): boolean {
    const parts = raw.split(/[\s,;]+/).filter(Boolean);
    const valid = parts.filter(isEmail);
    if (!valid.length) return false;
    add(
      valid.map((email) => {
        const saved = contacts.find(
          (c) => c.email.toLowerCase() === email.toLowerCase(),
        );
        return { email, name: saved?.name ?? null };
      }),
    );
    return true;
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === "ArrowDown" && options.length) {
      e.preventDefault();
      setOpen(true);
      setActive((i) => (i + 1) % options.length);
    } else if (e.key === "ArrowUp" && options.length) {
      e.preventDefault();
      setActive((i) => (i - 1 + options.length) % options.length);
    } else if (e.key === "Enter") {
      if (showList && options[active]) {
        e.preventDefault();
        add([options[active]]);
      } else if (query.trim()) {
        e.preventDefault();
        addTyped(query);
      }
    } else if ((e.key === "," || e.key === "Tab") && query.trim()) {
      if (addTyped(query)) e.preventDefault();
    } else if (e.key === "Backspace" && !query && value.length) {
      onChange(value.slice(0, -1));
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="relative">
      <div
        className={cn(
          "flex min-h-9 w-full flex-wrap items-center gap-1.5 rounded-md border border-input bg-background px-2 py-1.5 shadow-xs transition-[color,box-shadow] focus-within:border-ring focus-within:ring-[3px] focus-within:ring-ring/50",
          disabled && "pointer-events-none opacity-50",
        )}
        onClick={() => inputRef.current?.focus()}
      >
        {value.map((v) => (
          <span
            key={v.email.toLowerCase()}
            className="inline-flex max-w-full items-center gap-1 rounded-md border border-border bg-muted/60 py-0.5 pr-1 pl-2 text-xs text-foreground"
          >
            <span className="truncate">
              {v.name ? (
                <>
                  <span className="font-medium">{v.name}</span>{" "}
                  <span className="text-muted-foreground">{v.email}</span>
                </>
              ) : (
                v.email
              )}
            </span>
            <button
              type="button"
              aria-label={`Remove ${v.email}`}
              onClick={(e) => {
                e.stopPropagation();
                onChange(value.filter((x) => x !== v));
              }}
              className="rounded p-0.5 text-muted-foreground hover:bg-background hover:text-foreground"
            >
              <X className="size-3" />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          id={id}
          type="text"
          inputMode="email"
          autoComplete="off"
          autoFocus={autoFocus}
          disabled={disabled}
          role="combobox"
          aria-expanded={showList}
          aria-controls={listId}
          aria-autocomplete="list"
          value={query}
          placeholder={
            value.length
              ? "Add another…"
              : contacts.length
                ? "Search contacts or type an email…"
                : "Type an email and press Enter…"
          }
          onChange={(e) => {
            setQuery(e.target.value);
            setOpen(true);
            setActive(0);
          }}
          onPaste={(e) => {
            const text = e.clipboardData.getData("text");
            if (/[\s,;]/.test(text.trim()) && addTyped(text)) e.preventDefault();
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => {
            // Let a click on a suggestion land before the list closes.
            window.setTimeout(() => setOpen(false), 120);
            if (isEmail(query)) addTyped(query);
          }}
          onKeyDown={onKeyDown}
          className="h-6 min-w-40 flex-1 bg-transparent px-1 text-sm outline-none placeholder:text-muted-foreground"
        />
      </div>

      {showList ? (
        <ul
          id={listId}
          role="listbox"
          className="absolute inset-x-0 top-full z-50 mt-1 max-h-60 overflow-y-auto rounded-md border border-border bg-popover p-1 shadow-md"
        >
          {options.map((o, i) => {
            const isNew = i >= suggestions.length;
            return (
              <li
                key={o.email.toLowerCase()}
                role="option"
                aria-selected={i === active}
                onMouseDown={(e) => {
                  e.preventDefault();
                  add([o]);
                }}
                onMouseEnter={() => setActive(i)}
                className={cn(
                  "flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm",
                  i === active && "bg-accent",
                )}
              >
                {isNew ? (
                  <>
                    <Plus className="size-4 text-muted-foreground" />
                    <span>
                      Add <span className="font-medium">{o.email}</span>
                    </span>
                  </>
                ) : (
                  <>
                    <UserRound className="size-4 text-muted-foreground" />
                    <span className="min-w-0 truncate">
                      {o.name ? (
                        <>
                          <span className="font-medium">{o.name}</span>{" "}
                          <span className="text-muted-foreground">{o.email}</span>
                        </>
                      ) : (
                        o.email
                      )}
                    </span>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
