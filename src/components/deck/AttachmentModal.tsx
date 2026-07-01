import { useRef } from "react";
import { X } from "lucide-react";

import { API_BASE } from "@/lib/api";

import { useModalA11y } from "./use-modal-a11y";

const IMAGE_EXTS = new Set([".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"]);

function ext(path: string): string {
  const d = path.lastIndexOf(".");
  return d < 0 ? "" : path.slice(d).toLowerCase();
}

/** Uploaded references live under /api/attachments/; static deliverables under
 * the site base (public/deliverables/…). Mirrors app.ts source resolution. */
function resolveSrc(path: string, baseSlash: string): string {
  if (path.startsWith("attachments/")) {
    return `${API_BASE}/api/attachments/${path.slice("attachments/".length)}`;
  }
  return `${baseSlash}${path}`;
}

export function AttachmentModal({
  title,
  path,
  onClose,
}: {
  title: string;
  path: string;
  onClose: () => void;
}): React.ReactElement {
  const panelRef = useRef<HTMLDivElement>(null);
  useModalA11y(panelRef, onClose);

  const base = (import.meta.env.BASE_URL ?? "/") as string;
  const baseSlash = base.endsWith("/") ? base : `${base}/`;
  const src = resolveSrc(path, baseSlash);
  const isImage = IMAGE_EXTS.has(ext(path));

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${title} reference`}
      className="fixed inset-0 z-50 flex items-stretch justify-center"
    >
      <button
        type="button"
        aria-label="Close"
        tabIndex={-1}
        className="absolute inset-0 cursor-default bg-black/45"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        className="relative m-3 flex w-full max-w-4xl flex-col overflow-hidden rounded-xl bg-card shadow-lg outline-none"
      >
        <header className="flex items-center justify-between bg-foreground px-4 py-3 text-[color:var(--card)]">
          <span className="font-semibold">{title} reference</span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="flex size-8 items-center justify-center rounded-md hover:bg-white/10 [&_svg]:size-5"
          >
            <X aria-hidden="true" />
          </button>
        </header>
        {isImage ? (
          <img
            className="min-h-0 flex-1 bg-muted object-contain p-4"
            src={src}
            alt={`${title} reference`}
            loading="lazy"
          />
        ) : (
          <iframe
            className="min-h-0 flex-1 border-0 bg-muted"
            src={src}
            sandbox="allow-scripts"
            title={`${title} reference`}
            loading="lazy"
          />
        )}
      </div>
    </div>
  );
}
