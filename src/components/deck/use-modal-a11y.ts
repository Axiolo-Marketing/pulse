import { useEffect, type RefObject } from "react";

const FOCUSABLE =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * Minimal modal a11y for the deck's custom overlays: Esc to close, initial
 * focus into the dialog, Tab/Shift-Tab containment, and focus restored to the
 * opener on close. (`aria-modal` hides outside content from screen readers but
 * does NOT constrain browser focus — that's this hook's job.)
 */
export function useModalA11y(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
): void {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const opener = document.activeElement as HTMLElement | null;

    const focusables = (): HTMLElement[] =>
      Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (x) => !x.hasAttribute("disabled") && x.tabIndex !== -1,
      );

    (focusables()[0] ?? el).focus();

    const onKey = (e: KeyboardEvent): void => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const items = focusables();
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, [ref, onClose]);
}
