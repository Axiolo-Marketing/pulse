import { useState } from "react";
import { LoaderCircle } from "lucide-react";

import { ApiError, authApi, type AuthUser } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { readReturnTo, stashReturnTo } from "@/lib/return-to";
import { useOAuthProviders } from "@/lib/providers";

import { AuthLayout, OrDivider } from "./AuthLayout";

export function LoginView({
  onAuthed,
  onSignup,
  onForgot,
  notice,
}: {
  onAuthed: (user: AuthUser) => void;
  onSignup: () => void;
  onForgot: () => void;
  /** Error shown on arrival (e.g. from an OAuth redirect). */
  notice?: string | null;
}): React.ReactElement {
  const providers = useOAuthProviders();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(notice ?? null);
  const [submitting, setSubmitting] = useState(false);

  function startOAuth(provider: "google" | "microsoft"): void {
    // Carry a same-origin return_to (MCP consent) across the provider
    // round-trip; the backend redirects back to plain /admin/.
    stashReturnTo(readReturnTo());
    window.location.href = authApi.oauthAuthorizeUrl(provider);
  }

  async function submit(): Promise<void> {
    setError(null);
    setSubmitting(true);
    try {
      onAuthed(await authApi.login(email, password));
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not sign in.");
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout>
      <CardHeader>
        <CardTitle>Sign in</CardTitle>
        <CardDescription>Welcome back to Pulse.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {providers.google || providers.microsoft ? (
          <>
            <div className="flex flex-col gap-2">
              {providers.google ? (
                <Button
                  variant="outline"
                  type="button"
                  onClick={() => startOAuth("google")}
                >
                  Continue with Google
                </Button>
              ) : null}
              {providers.microsoft ? (
                <Button
                  variant="outline"
                  type="button"
                  onClick={() => startOAuth("microsoft")}
                >
                  Continue with Microsoft
                </Button>
              ) : null}
            </div>
            <OrDivider />
          </>
        ) : null}
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
          noValidate
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="login-email">Email</Label>
            <Input
              id="login-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={submitting}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="login-pw">Password</Label>
            <Input
              id="login-pw"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
            />
          </div>
          {error ? (
            <p className="text-sm font-medium text-destructive" role="alert">
              {error}
            </p>
          ) : null}
          <Button type="submit" disabled={submitting}>
            {submitting ? (
              <LoaderCircle className="animate-spin" aria-hidden="true" />
            ) : null}
            Sign in
          </Button>
        </form>
        <div className="flex justify-between text-sm">
          <button
            type="button"
            onClick={onForgot}
            className="text-primary hover:underline"
          >
            Forgot password?
          </button>
          <button
            type="button"
            onClick={onSignup}
            className="text-primary hover:underline"
          >
            Create account
          </button>
        </div>
      </CardContent>
    </AuthLayout>
  );
}
