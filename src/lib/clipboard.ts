/** Write text to the clipboard, where the text may not be known yet (e.g. it
 * needs a fetch first). Safari drops clipboard access once a click handler
 * awaits anything, so hand the clipboard a *promise* via ClipboardItem
 * (called synchronously inside the gesture); fall back to writeText where
 * ClipboardItem isn't available. Resolves to the copied text; rejects if the
 * text promise rejects or the browser refuses the write. */
export async function copyText(
  text: string | Promise<string>,
): Promise<string> {
  const pending = Promise.resolve(text);
  if (typeof ClipboardItem !== "undefined" && navigator.clipboard?.write) {
    await navigator.clipboard.write([
      new ClipboardItem({
        "text/plain": pending.then(
          (t) => new Blob([t], { type: "text/plain" }),
        ),
      }),
    ]);
    return pending;
  }
  const t = await pending;
  await navigator.clipboard.writeText(t);
  return t;
}

/** A respondent's deck URL for their magic-link token. */
export function deckUrl(token: string): string {
  return `${window.location.origin}/?t=${token}`;
}
