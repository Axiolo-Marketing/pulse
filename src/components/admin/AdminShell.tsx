import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HashRouter, Link, Navigate, Route, Routes } from "react-router-dom";

import { toast } from "sonner";

import { ApiError, authApi, orgsApi, type AuthUser } from "@/lib/api";
import { applyBranding } from "@/lib/branding";

import { AdminFooter, PulseWordmark } from "./Brand";
import { EngagementDetail } from "./EngagementDetail";
import { EngagementList } from "./EngagementList";
import { OrgSwitcher } from "./OrgSwitcher";
import { SettingsPage } from "./settings/SettingsPage";
import { SuperadminPage } from "./superadmin/SuperadminPage";
import { UserMenu } from "./UserMenu";

export function AdminShell({ user }: { user: AuthUser }): React.ReactElement {
  const qc = useQueryClient();
  const orgsQ = useQuery({
    queryKey: ["orgs", "mine"],
    queryFn: () => orgsApi.listMine(),
  });
  const orgMeQ = useQuery({
    queryKey: ["orgs", "me"],
    queryFn: () => orgsApi.me(),
  });

  useEffect(() => {
    if (orgMeQ.data) applyBranding(orgMeQ.data.branding);
  }, [orgMeQ.data]);

  const switchM = useMutation({
    mutationFn: (orgId: string) => orgsApi.switchOrg(orgId),
    onSuccess: async (_data, orgId) => {
      await qc.invalidateQueries(); // refetch everything under the new org
      const name = orgsQ.data?.find((o) => o.id === orgId)?.name;
      toast.success(name ? `Switched to ${name}` : "Switched organization");
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.detail : "Could not switch"),
  });

  async function signOut(): Promise<void> {
    try {
      await authApi.logout();
    } catch {
      /* ignore — clear the session locally regardless */
    }
    await qc.invalidateQueries({ queryKey: ["auth", "me"] });
  }

  return (
    <HashRouter>
      <div className="flex min-h-dvh flex-col bg-background">
        <header className="sticky top-0 z-10 border-b border-border bg-background/95 backdrop-blur">
          <div className="mx-auto flex h-14 w-full max-w-6xl items-center justify-between gap-3 px-4">
            <div className="flex items-center gap-4">
              <Link to="/" aria-label="Pulse home">
                <PulseWordmark />
              </Link>
              <span aria-hidden="true" className="h-5 w-px bg-border" />
              <OrgSwitcher
                orgs={orgsQ.data ?? []}
                activeOrgId={user.active_org_id}
                pending={switchM.isPending}
                onSwitch={(id) => switchM.mutate(id)}
              />
            </div>
            <nav className="flex items-center">
              <UserMenu user={user} onSignOut={() => void signOut()} />
            </nav>
          </div>
        </header>
        <Routes>
          <Route path="/" element={<EngagementList />} />
          <Route path="/client/:id" element={<EngagementDetail />} />
          <Route
            path="/settings"
            element={<Navigate to="/settings/personal" replace />}
          />
          <Route path="/settings/:tab" element={<SettingsPage />} />
          <Route
            path="/superadmin"
            element={
              user.is_superadmin ? (
                <SuperadminPage />
              ) : (
                <Navigate to="/" replace />
              )
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <AdminFooter />
      </div>
    </HashRouter>
  );
}
