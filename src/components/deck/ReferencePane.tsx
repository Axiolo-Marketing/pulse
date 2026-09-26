import { Download, ExternalLink, FileText } from "lucide-react";

import { referenceSrc, isImageReference } from "./AttachmentModal";

/** Wide-screen reference viewer, shown beside the question instead of the
 * pop-up. Same sandboxing as the pop-up (uploaded HTML is also served with a
 * CSP sandbox, so "Open in new tab" is equally isolated). */
export function ReferencePane({
  title,
  path,
}: {
  title: string;
  path: string;
}): React.ReactElement {
  const src = referenceSrc(path);
  const fileName = path.split("/").pop() || "reference";
  return (
    <section
      aria-label={`${title} reference`}
      className="flex h-full min-h-[520px] flex-col overflow-hidden rounded-xl border border-border bg-card"
    >
      <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
          Reference
        </span>
        <a
          href={src}
          download={fileName}
          className="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground [&_svg]:size-3.5"
        >
          <Download aria-hidden="true" />
          Download
        </a>
        <a
          href={src}
          target="_blank"
          rel="noopener noreferrer"
          title="Open in new tab"
          className="flex shrink-0 items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground [&_svg]:size-3.5"
        >
          <ExternalLink aria-hidden="true" />
          Open
        </a>
      </header>
      {isImageReference(path) ? (
        <img
          className="min-h-0 flex-1 bg-muted object-contain p-4"
          src={src}
          alt={`${title} reference`}
        />
      ) : (
        <iframe
          className="min-h-0 flex-1 border-0 bg-muted"
          src={src}
          sandbox="allow-scripts"
          title={`${title} reference`}
        />
      )}
    </section>
  );
}
