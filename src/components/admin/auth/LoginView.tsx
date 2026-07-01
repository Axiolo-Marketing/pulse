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

import { AuthLayout, OrDivider } from "./AuthLayout";

export function LoginView({
  onAuthed,
  onSignup,
  onForgot,
}: {
  onAuthed: (user: AuthUser) => void;
  onSignup: () => void;
  onForgot: () => void;
}): React.ReactElement {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

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
        <div className="flex flex-col gap-2">
          <Button
            variant="outline"
            type="button"
            onClick={() => {
              window.location.href = authApi.oauthAuthorizeUrl("google");
            }}
          >
            Continue with Google
          </Button>
          <Button
            variant="outline"
            type="button"
            onClick={() => {
              window.location.href = authApi.oauthAuthorizeUrl("microsoft");
            }}
          >
            Continue with Microsoft
          </Button>
        </div>
        <OrDivider />
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
