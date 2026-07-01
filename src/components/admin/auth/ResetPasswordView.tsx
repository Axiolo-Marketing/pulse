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

export function ResetPasswordView({
  token,
}: {
  token: string;
}): React.ReactElement {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(): Promise<void> {
    setError(null);
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setSubmitting(true);
    try {
      await authApi.resetPassword(token, password);
      setDone(true);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Could not reset your password.",
      );
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout>
      {done ? (
        <>
          <CardHeader className="items-center gap-3 text-center">
            <span className="flex size-11 items-center justify-center rounded-full bg-success-soft text-success [&_svg]:size-6">
              <CircleCheck aria-hidden="true" />
            </span>
            <CardTitle>Password updated</CardTitle>
            <CardDescription>
              You can now sign in with your new password.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              className="w-full"
              onClick={() => {
                window.location.href = "/v2/admin";
              }}
            >
              Continue to sign in
            </Button>
          </CardContent>
        </>
      ) : (
        <>
          <CardHeader>
            <CardTitle>Set a new password</CardTitle>
            <CardDescription>
              Choose a new password for your account.
            </CardDescription>
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
                <Label htmlFor="rp-pw">New password</Label>
                <Input
                  id="rp-pw"
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={submitting}
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="rp-confirm">Confirm password</Label>
                <Input
                  id="rp-confirm"
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  required
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
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
                Update password
              </Button>
            </form>
          </CardContent>
        </>
      )}
    </AuthLayout>
  );
}
