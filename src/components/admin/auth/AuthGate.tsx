import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { AuthUser } from "@/lib/api";

import { ForgotView } from "./ForgotView";
import { LoginView } from "./LoginView";
import { SignupView } from "./SignupView";

const OAUTH_ERRORS: Record<string, string> = {
  invitation_required:
    "You need an invitation to use Pulse. Ask an org owner to invite you, then click the link in the email.",
  invite_invalid:
    "That invitation link is invalid or has expired. Ask an org owner to send a new one.",
  email_unverified:
    "We couldn't verify your email with that provider. Verify your email address there, or sign in with your password.",
};

type View = "login" | "signup" | "forgot";

/** Signed-out auth flow. On successful sign-in, seed the auth.me query so the
 * top-level Gate re-renders into the shell. */
export function AuthGate(): React.ReactElement {
  const qc = useQueryClient();
  const [view, setView] = useState<View>("login");
  const [notice] = useState<string | null>(() => {
    const code = new URLSearchParams(window.location.search).get("error");
    return code ? (OAUTH_ERRORS[code] ?? null) : null;
  });

  // Strip the param so a refresh doesn't repeat the message.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("error")) {
      url.searchParams.delete("error");
      window.history.replaceState({}, "", url.toString());
    }
  }, []);

  if (view === "signup") return <SignupView onBack={() => setView("login")} />;
  if (view === "forgot") return <ForgotView onBack={() => setView("login")} />;
  return (
    <LoginView
      notice={notice}
      onAuthed={(user: AuthUser) => qc.setQueryData(["auth", "me"], user)}
      onSignup={() => setView("signup")}
      onForgot={() => setView("forgot")}
    />
  );
}
