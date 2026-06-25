// Recipient-facing unsubscribe page. Reads the signed `?u=` token from the
// reminder/invite email link and POSTs it to the API, which flips the
// recipient's `unsubscribed_at`. All copy here is static — no user input is
// interpolated, so there's nothing to escape.
import { API_BASE } from "../lib/api";

const mount = document.getElementById("unsubscribe-mount");

function renderCard(title: string, body: string): void {
  if (!mount) return;
  mount.innerHTML = `
    <div class="invite-page-shell">
      <main class="invite-page">
        <div class="invite-card">
          <h1 class="invite-h">${title}</h1>
          <p class="invite-body">${body}</p>
        </div>
      </main>
    </div>`;
}

async function main(): Promise<void> {
  const token = new URLSearchParams(window.location.search).get("u");
  if (!token) {
    renderCard("Invalid link", "This unsubscribe link is missing its code.");
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/reminders/unsubscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (!res.ok) {
      renderCard("Link expired", "This unsubscribe link is invalid or has expired.");
      return;
    }
    renderCard(
      "You're unsubscribed",
      "You won't receive any more reminders about this. You can still open your questions from your original link whenever you're ready.",
    );
  } catch {
    renderCard("Something went wrong", "Please try again in a moment.");
  }
}

void main();
