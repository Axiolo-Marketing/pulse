import { useQuery } from "@tanstack/react-query";
import { Activity, Building2, UserRound } from "lucide-react";
import { NavLink, useParams } from "react-router-dom";

import { authApi, orgsApi } from "@/lib/api";
import { cn } from "@/lib/utils";

import { AdminError, AdminLoading } from "../states";
import { ActivityFeed } from "./ActivityFeed";
import { OrganizationTab } from "./OrganizationTab";
import { PersonalTab } from "./PersonalTab";

const TABS = [
  {
    id: "personal",
    label: "Account",
    icon: UserRound,
    title: "Account",
    description: "Your profile, sign-in methods, and API keys.",
  },
  {
    id: "organization",
    label: "Organization",
    icon: Building2,
    title: "Organization",
    description: "Name, branding, and the people in your organization.",
  },
  {
    id: "activity",
    label: "Activity",
    icon: Activity,
    title: "Activity",
    description: "Every change made in your organization, newest first.",
  },
] as const;
type TabId = (typeof TABS)[number]["id"];

export function SettingsPage(): React.ReactElement {
  const { tab } = useParams();
  const active = TABS.find((t) => t.id === tab) ?? TABS[0];

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

  const content: Record<TabId, React.ReactNode> = {
    personal: <PersonalTab user={userQ.data} orgName={orgQ.data.name} />,
    organization: (
      <OrganizationTab org={orgQ.data} currentUserId={userQ.data.id} />
    ),
    activity: <ActivityFeed />,
  };

  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8">
      <h1 className="text-2xl font-semibold tracking-tight text-foreground">
        Settings
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Manage your account and {orgQ.data.name}.
      </p>

      <div className="mt-6 grid gap-6 md:grid-cols-[200px_minmax(0,1fr)] md:gap-10">
        <nav
          aria-label="Settings sections"
          className="-mx-4 flex gap-1 overflow-x-auto border-b border-border px-4 md:mx-0 md:flex-col md:self-start md:border-b-0 md:px-0 md:sticky md:top-20"
        >
          {TABS.map((t) => (
            <NavLink
              key={t.id}
              to={`/settings/${t.id}`}
              className={({ isActive }) =>
                cn(
                  "flex shrink-0 items-center gap-2 whitespace-nowrap px-3 py-2 text-sm transition-colors",
                  // Mobile: underline tabs. Desktop: filled pill rows.
                  "-mb-px border-b-2 md:mb-0 md:rounded-md md:border-b-0",
                  isActive || (t.id === active.id)
                    ? "border-foreground font-medium text-foreground md:bg-muted"
                    : "border-transparent text-muted-foreground hover:text-foreground md:hover:bg-muted/60",
                )
              }
            >
              <t.icon className="size-4" aria-hidden="true" />
              {t.label}
            </NavLink>
          ))}
        </nav>

        <div className="min-w-0">
          <div className="mb-5 hidden md:block">
            <h2 className="text-lg font-semibold text-foreground">{active.title}</h2>
            <p className="text-sm text-muted-foreground">{active.description}</p>
          </div>
          {content[active.id]}
        </div>
      </div>
    </main>
  );
}
