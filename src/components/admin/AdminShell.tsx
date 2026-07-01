import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { HashRouter, Link, Navigate, Route, Routes } from "react-router-dom";

import { authApi, orgsApi, type AuthUser } from "@/lib/api";
import { applyBranding } from "@/lib/branding";

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

  async function switchOrg(orgId: string): Promise<void> {
    await orgsApi.switchOrg(orgId);
    await qc.invalidateQueries(); // refetch everything under the new org
  }

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
        <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-border bg-card/95 px-4 py-2.5 backdrop-blur">
          <div className="flex items-center gap-3">
            <Link
              to="/"
              className="flex items-center gap-2 text-base font-semibold text-foreground"
            >
              <img src="/axiolo-logo.svg" alt="Axiolo" width="72" height="20" />
              <span aria-hidden="true" className="text-muted-foreground">
                ·
              </span>
              Pulse
              <span className="font-normal text-muted-foreground">Admin</span>
            </Link>
            <OrgSwitcher
              orgs={orgsQ.data ?? []}
              activeOrgId={user.active_org_id}
              onSwitch={(id) => void switchOrg(id)}
            />
          </div>
          <nav className="flex items-center">
            <UserMenu user={user} onSignOut={() => void signOut()} />
          </nav>
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
      </div>
    </HashRouter>
  );
}
