import { useEffect, useState } from "react";
import { CircleCheck, LoaderCircle, TriangleAlert } from "lucide-react";

import { ApiError, authApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

import { AuthLayout } from "./AuthLayout";

type Status = "verifying" | "success" | "error";

export function VerifyEmailView({
  token,
}: {
  token: string;
}): React.ReactElement {
  const [status, setStatus] = useState<Status>("verifying");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function run(): Promise<void> {
      try {
        await authApi.verifyEmail(token);
        if (!cancelled) setStatus("success");
      } catch (err) {
        if (cancelled) return;
        setError(
          err instanceof ApiError ? err.detail : "Could not verify your email.",
        );
        setStatus("error");
      }
    }
    void run();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <AuthLayout>
      {status === "verifying" ? (
        <CardHeader className="items-center gap-3 text-center">
          <span className="flex size-11 items-center justify-center rounded-full bg-muted text-muted-foreground [&_svg]:size-6">
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          </span>
          <CardTitle>Verifying your email…</CardTitle>
        </CardHeader>
      ) : status === "success" ? (
        <>
          <CardHeader className="items-center gap-3 text-center">
            <span className="flex size-11 items-center justify-center rounded-full bg-success-soft text-success [&_svg]:size-6">
              <CircleCheck aria-hidden="true" />
            </span>
            <CardTitle>Email verified</CardTitle>
            <CardDescription>
              Your email address has been confirmed.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              className="w-full"
              onClick={() => {
                window.location.href = "/v2/admin";
              }}
            >
              Continue to admin
            </Button>
          </CardContent>
        </>
      ) : (
        <>
          <CardHeader className="items-center gap-3 text-center">
            <span className="flex size-11 items-center justify-center rounded-full bg-warning-soft text-warning [&_svg]:size-6">
              <TriangleAlert aria-hidden="true" />
            </span>
            <CardTitle>Verification failed</CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button
              variant="outline"
              className="w-full"
              onClick={() => {
                window.location.href = "/v2/admin";
              }}
            >
              Back to sign in
            </Button>
          </CardContent>
        </>
      )}
    </AuthLayout>
  );
}
