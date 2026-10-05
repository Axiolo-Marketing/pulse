import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, authApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { honorReturnTo } from "@/lib/return-to";

import { AdminShell } from "./AdminShell";
import { AuthGate } from "./auth/AuthGate";
import { AdminError, AdminLoading } from "./states";

/** Auth gate: signed-out → auth views; signed-in w/o org → error; else shell. */
export function Gate(): React.ReactElement {
  const qc = useQueryClient();
  const meQ = useQuery({ queryKey: ["auth", "me"], queryFn: () => authApi.me() });
  const hasOrg = !!meQ.data?.active_org_id;
  const [redirecting, setRedirecting] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  // MCP OAuth consent (a backend route, not a router route) bounces signed-out
  // operators here with ?return_to=; once signed in with an org, send them back.
  useEffect(() => {
    if (hasOrg && honorReturnTo()) setRedirecting(true);
  }, [hasOrg]);

  async function signOut(): Promise<void> {
    setSigningOut(true);
    try {
      await authApi.logout();
    } catch {
      /* clear locally regardless */
    }
    await qc.invalidateQueries({ queryKey: ["auth", "me"] });
    setSigningOut(false);
  }

  if (meQ.isPending || redirecting) return <AdminLoading />;
  if (meQ.isError) {
    const err = meQ.error;
    if (err instanceof ApiError && err.status !== 401 && err.status !== 403) {
      return <AdminError title="Something went wrong" body={err.detail} />;
    }
    return <AuthGate />;
  }
  if (!meQ.data.active_org_id) {
    return (
      <AdminError
        title="No organization yet"
        body="Your account isn't part of an organization. Ask an owner to invite you, then sign in again."
      >
        <Button
          variant="outline"
          onClick={() => void signOut()}
          disabled={signingOut}
        >
          Sign out
        </Button>
      </AdminError>
    );
  }
  return <AdminShell user={meQ.data} />;
}
