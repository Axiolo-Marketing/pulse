import { useState } from "react";
import { CircleCheck, LoaderCircle } from "lucide-react";

import { authApi } from "@/lib/api";
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

export function ForgotView({
  onBack,
}: {
  onBack: () => void;
}): React.ReactElement {
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(): Promise<void> {
    setSubmitting(true);
    // The backend always returns 200 (no email enumeration).
    try {
      await authApi.forgotPassword(email);
    } catch {
      /* ignore — still show the confirmation */
    }
    setDone(true);
  }

  return (
    <AuthLayout>
      {done ? (
        <>
          <CardHeader className="items-center gap-3 text-center">
            <span className="flex size-11 items-center justify-center rounded-full bg-success-soft text-success [&_svg]:size-6">
              <CircleCheck aria-hidden="true" />
            </span>
            <CardTitle>Check your email</CardTitle>
            <CardDescription>
              If an account exists for {email}, we sent a reset link.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" className="w-full" onClick={onBack}>
              Back to sign in
            </Button>
          </CardContent>
        </>
      ) : (
        <>
          <CardHeader>
            <CardTitle>Reset password</CardTitle>
            <CardDescription>
              Enter your email and we'll send a reset link.
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
                <Label htmlFor="fp-email">Email</Label>
                <Input
                  id="fp-email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={submitting}
                />
              </div>
              <Button type="submit" disabled={submitting}>
                {submitting ? (
                  <LoaderCircle className="animate-spin" aria-hidden="true" />
                ) : null}
                Send reset link
              </Button>
            </form>
            <button
              type="button"
              onClick={onBack}
              className="mt-4 text-sm text-primary hover:underline"
            >
              Back to sign in
            </button>
          </CardContent>
        </>
      )}
    </AuthLayout>
  );
}
