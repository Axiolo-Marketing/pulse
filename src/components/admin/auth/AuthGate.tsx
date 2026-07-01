import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { AuthUser } from "@/lib/api";

import { ForgotView } from "./ForgotView";
import { LoginView } from "./LoginView";
import { SignupView } from "./SignupView";

type View = "login" | "signup" | "forgot";

/** Signed-out auth flow. On successful sign-in, seed the auth.me query so the
 * top-level Gate re-renders into the shell. */
export function AuthGate(): React.ReactElement {
  const qc = useQueryClient();
  const [view, setView] = useState<View>("login");

  if (view === "signup") return <SignupView onBack={() => setView("login")} />;
  if (view === "forgot") return <ForgotView onBack={() => setView("login")} />;
  return (
    <LoginView
      onAuthed={(user: AuthUser) => qc.setQueryData(["auth", "me"], user)}
      onSignup={() => setView("signup")}
      onForgot={() => setView("forgot")}
    />
  );
}
