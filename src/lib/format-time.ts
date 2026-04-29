// Display timestamps in the operator's local timezone.
// Per spec §14.4: relative for <24h, absolute with timezone for older.

export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const dayMs = 24 * 60 * 60 * 1000;

  if (diffMs >= 0 && diffMs < dayMs) {
    return formatRelative(diffMs);
  }
  return formatAbsolute(date);
}

function formatRelative(diffMs: number): string {
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.floor(minutes / 60);
  return `${hours} hour${hours === 1 ? "" : "s"} ago`;
}

function formatAbsolute(date: Date): string {
  // "April 23, 2026 at 4:15 PM CDT"
  const dateFmt = new Intl.DateTimeFormat(undefined, {
    month: "long",
    day: "numeric",
    year: "numeric",
  });
  const timeFmt = new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  });
  return `${dateFmt.format(date)} at ${timeFmt.format(date)}`;
}
