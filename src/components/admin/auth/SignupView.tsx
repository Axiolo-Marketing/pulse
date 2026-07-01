import { useState } from "react";
import { CircleCheck, LoaderCircle } from "lucide-react";

import { ApiError, authApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { AuthLayout } from "./AuthLayout";

export function SignupView({
  onBack,
}: {
  onBack: () => void;
}): React.ReactElement {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(): Promise<void> {
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      await authApi.signup({ email, password, name: name.trim() || undefined });
      setDone(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Could not sign up.");
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <AuthLayout>
        <CardHeader className="items-center gap-3 text-center">
          <span className="flex size-11 items-center justify-center rounded-full bg-success-soft text-success [&_svg]:size-6">
            <CircleCheck aria-hidden="true" />
          </span>
          <CardTitle>Check your email</CardTitle>
          <CardDescription>
            We sent a verification link to {email}. Confirm it, then sign in.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" className="w-full" onClick={onBack}>
            Back to sign in
          </Button>
        </CardContent>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout>
      <CardHeader>
        <CardTitle>Create account</CardTitle>
        <CardDescription>Sign up to manage your engagements.</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
          noValidate
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="su-name">Name (optional)</Label>
            <Input
              id="su-name"
              type="text"
              autoComplete="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={submitting}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="su-email">Email</Label>
            <Input
              id="su-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={submitting}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="su-pw">Password (8+ characters)</Label>
            <Input
              id="su-pw"
              type="password"
              autoComplete="new-password"
              minLength={8}
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
            Create account
          </Button>
        </form>
        <button
          type="button"
          onClick={onBack}
          className="mt-4 text-sm text-primary hover:underline"
        >
          Already have an account? Sign in
        </button>
      </CardContent>
    </AuthLayout>
  );
}
