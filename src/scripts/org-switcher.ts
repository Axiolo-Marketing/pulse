// Active-org switcher pinned in the admin shell header.
//
// Two display modes driven by membership count:
//   - 1 org → static pill (no menu, no caret, not focusable). Communicates
//     "you are scoped to <org>" without inviting interaction.
//   - 2+ orgs → button + popover. Activates on click, Enter, Space.
//
// The menu is a lightweight popover (no portaling, no focus trap library):
// keydown handlers route ↑/↓ between items, Escape closes, Tab/blur closes.
// Selecting an item calls the supplied `onSwitch` async callback; the
// container is expected to re-render after the promise settles.
import { orgLogoUrl, type OrgSummary } from "../lib/api";

const escape = (s: string): string =>
  s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");

export interface OrgSwitcherDeps {
  /** Every org the operator is a member of. */
  orgs: OrgSummary[];
  /** Currently-active org id; the switcher draws this one as selected. */
  activeOrgId: string | null;
  /** Called when the operator picks a different org. The host is
   * responsible for re-fetching the user surface and re-rendering. */
  onSwitch: (orgId: string) => Promise<void> | void;
}

/** Render the switcher into the header slot. Returns nothing — the
 * caller already has a parent element bound. Handlers are attached
 * once per call; re-calling rewires from scratch (safe). */
export function renderOrgSwitcher(
  slot: HTMLElement,
  deps: OrgSwitcherDeps,
): void {
  const { orgs, activeOrgId } = deps;

  // Edge case: the user has zero orgs. The host already guards on this
  // (the shell shouldn't even load), but render nothing rather than
  // throw if it somehow reaches us.
  if (orgs.length === 0) {
    slot.innerHTML = "";
    return;
  }

  const active =
    orgs.find((o) => o.id === activeOrgId) ?? orgs[0];

  if (orgs.length === 1) {
    slot.innerHTML = renderStaticPill(active);
    return;
  }

  slot.innerHTML = renderSwitcherButton(active, orgs);
  attachHandlers(slot, deps);
}

function renderLogoMark(org: OrgSummary, size: number): string {
  const url = orgLogoUrl(org.logo_path);
  if (url) {
    return `<img class="org-switcher-logo" src="${escape(url)}" alt="" width="${size}" height="${size}" loading="lazy" />`;
  }
  const initial = (org.name || "?").trim().charAt(0).toUpperCase();
  return `<span class="org-switcher-logo org-switcher-logo--fallback" aria-hidden="true">${escape(initial)}</span>`;
}

function renderStaticPill(org: OrgSummary): string {
  return `
    <div class="org-switcher org-switcher--static" aria-label="Active organization: ${escape(org.name)}">
      ${renderLogoMark(org, 20)}
      <span class="org-switcher-name">${escape(org.name)}</span>
    </div>
  `;
}

function renderSwitcherButton(active: OrgSummary, orgs: OrgSummary[]): string {
  const menuId = "org-switcher-menu";
  const itemsHtml = orgs
    .map((o) => {
      const isActive = o.id === active.id;
      return `
        <li role="none">
          <button
            class="org-switcher-item${isActive ? " is-active" : ""}"
            role="menuitemradio"
            type="button"
            aria-checked="${isActive ? "true" : "false"}"
            data-org-id="${escape(o.id)}"
            tabindex="-1"
          >
            ${renderLogoMark(o, 24)}
            <span class="org-switcher-item-text">
              <span class="org-switcher-item-name">${escape(o.name)}</span>
              <span class="org-switcher-item-role">${escape(o.role)}</span>
            </span>
            ${isActive ? `<span class="org-switcher-item-mark" aria-hidden="true">✓</span>` : ""}
          </button>
        </li>
      `;
    })
    .join("");

  return `
    <div class="org-switcher" data-state="closed">
      <button
        type="button"
        class="org-switcher-trigger"
        aria-haspopup="menu"
        aria-expanded="false"
        aria-controls="${menuId}"
        data-action="org-switcher-toggle"
      >
        ${renderLogoMark(active, 20)}
        <span class="org-switcher-name">${escape(active.name)}</span>
        <span class="org-switcher-caret" aria-hidden="true">▾</span>
      </button>
      <ul
        id="${menuId}"
        class="org-switcher-menu"
        role="menu"
        aria-label="Switch organization"
        hidden
      >${itemsHtml}</ul>
    </div>
  `;
}

function attachHandlers(slot: HTMLElement, deps: OrgSwitcherDeps): void {
  const root = slot.querySelector<HTMLElement>(".org-switcher");
  if (!root) return;

  const trigger = root.querySelector<HTMLButtonElement>(
    ".org-switcher-trigger",
  );
  const menu = root.querySelector<HTMLUListElement>(".org-switcher-menu");
  if (!trigger || !menu) return;

  const items = Array.from(
    menu.querySelectorAll<HTMLButtonElement>(".org-switcher-item"),
  );

  const open = (focusIndex?: number): void => {
    if (root.dataset.state === "open") return;
    root.dataset.state = "open";
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    const idx =
      focusIndex !== undefined
        ? focusIndex
        : items.findIndex((it) => it.classList.contains("is-active"));
    const target = items[idx >= 0 ? idx : 0];
    target?.focus();
    document.addEventListener("click", outsideClick, true);
    document.addEventListener("keydown", keyHandler, true);
  };

  const close = (returnFocus = true): void => {
    if (root.dataset.state !== "open") return;
    root.dataset.state = "closed";
    menu.hidden = true;
    trigger.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", outsideClick, true);
    document.removeEventListener("keydown", keyHandler, true);
    if (returnFocus) trigger.focus();
  };

  const outsideClick = (e: MouseEvent): void => {
    if (!root.contains(e.target as Node)) close(false);
  };

  const keyHandler = (e: KeyboardEvent): void => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key === "Tab") {
      // Let Tab move focus naturally, but close the popover so it
      // doesn't trap the user inside.
      close(false);
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const currentIdx = items.findIndex(
        (it) => it === document.activeElement,
      );
      const next =
        e.key === "ArrowDown"
          ? (currentIdx + 1) % items.length
          : (currentIdx - 1 + items.length) % items.length;
      items[next]?.focus();
      return;
    }
    if (e.key === "Home") {
      e.preventDefault();
      items[0]?.focus();
      return;
    }
    if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1]?.focus();
      return;
    }
  };

  trigger.addEventListener("click", (e) => {
    e.preventDefault();
    if (root.dataset.state === "open") close();
    else open();
  });

  for (const item of items) {
    item.addEventListener("click", async (e) => {
      e.preventDefault();
      const orgId = item.dataset.orgId;
      if (!orgId) return;
      if (orgId === deps.activeOrgId) {
        close();
        return;
      }
      // Optimistic UX: disable items so a second click can't queue a
      // duplicate switch. Don't close yet — the host re-renders on
      // success and the new instance starts closed.
      for (const it of items) it.setAttribute("aria-disabled", "true");
      try {
        await deps.onSwitch(orgId);
      } catch {
        // Host shows a toast; just re-enable the items.
        for (const it of items) it.removeAttribute("aria-disabled");
      }
    });
  }
}
