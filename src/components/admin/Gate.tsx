import { useQuery } from "@tanstack/react-query";

import { ApiError, authApi } from "@/lib/api";

import { AdminShell } from "./AdminShell";
import { AuthGate } from "./auth/AuthGate";
import { AdminError, AdminLoading } from "./states";

/** Auth gate: signed-out → auth views; signed-in w/o org → error; else shell. */
export function Gate(): React.ReactElement {
  const meQ = useQuery({ queryKey: ["auth", "me"], queryFn: () => authApi.me() });

  if (meQ.isPending) return <AdminLoading />;
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
        body="Your account isn't part of an organization. Ask an owner to invite you, then refresh."
      />
    );
  }
  return <AdminShell user={meQ.data} />;
}
