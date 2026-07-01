import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";

import { authApi, orgsApi } from "@/lib/api";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { AdminError, AdminLoading } from "../states";
import { ActivityFeed } from "./ActivityFeed";
import { OrganizationTab } from "./OrganizationTab";
import { PersonalTab } from "./PersonalTab";

const TABS = ["personal", "organization", "activity"] as const;
type Tab = (typeof TABS)[number];

export function SettingsPage(): React.ReactElement {
  const navigate = useNavigate();
  const { tab } = useParams();
  const active: Tab = (TABS as readonly string[]).includes(tab ?? "")
    ? (tab as Tab)
    : "personal";

  const userQ = useQuery({ queryKey: ["auth", "me"], queryFn: () => authApi.me() });
  const orgQ = useQuery({ queryKey: ["orgs", "me"], queryFn: () => orgsApi.me() });

  if (userQ.isPending || orgQ.isPending) return <AdminLoading />;
  if (userQ.isError || orgQ.isError) {
    return (
      <AdminError
        title="Couldn't load settings"
        body="Please refresh and try again."
      />
    );
  }

  return (
    <main className="mx-auto w-full max-w-3xl px-4 py-6">
      <h1 className="mb-4 text-xl font-bold text-foreground">Settings</h1>
      <Tabs value={active} onValueChange={(v) => navigate(`/settings/${v}`)}>
        <TabsList>
          <TabsTrigger value="personal">Personal</TabsTrigger>
          <TabsTrigger value="organization">Organization</TabsTrigger>
          <TabsTrigger value="activity">Activity</TabsTrigger>
        </TabsList>
        <TabsContent value="personal" className="mt-6">
          <PersonalTab user={userQ.data} orgName={orgQ.data.name} />
        </TabsContent>
        <TabsContent value="organization" className="mt-6">
          <OrganizationTab org={orgQ.data} currentUserId={userQ.data.id} />
        </TabsContent>
        <TabsContent value="activity" className="mt-6">
          <ActivityFeed />
        </TabsContent>
      </Tabs>
    </main>
  );
}
