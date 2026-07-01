import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { Toaster } from "@/components/ui/sonner";

import { Gate } from "./Gate";
import { ResetPasswordView } from "./auth/ResetPasswordView";
import { VerifyEmailView } from "./auth/VerifyEmailView";

export default function AdminApp(): React.ReactElement {
  const [qc] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: false,
            refetchOnWindowFocus: false,
            staleTime: 30_000,
          },
        },
      }),
  );

  const params = new URLSearchParams(window.location.search);
  const verifyToken = params.get("verify-email-token");
  const resetToken = params.get("reset-password-token");

  return (
    <QueryClientProvider client={qc}>
      {verifyToken ? (
        <VerifyEmailView token={verifyToken} />
      ) : resetToken ? (
        <ResetPasswordView token={resetToken} />
      ) : (
        <Gate />
      )}
      <Toaster richColors position="top-center" />
    </QueryClientProvider>
  );
}
