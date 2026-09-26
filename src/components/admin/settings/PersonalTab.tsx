import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  ApiError,
  authApi,
  type AuthUser,
  type OAuthIdentitySummary,
} from "@/lib/api";
import { formatTimestamp } from "@/lib/format-time";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import { ApiKeysManager } from "./ApiKeysManager";
import { FormMessage, Pill, SettingsSection } from "./parts";

type Msg = { kind: "success" | "error"; text: string } | null;

const PROVIDER_LABELS: Record<string, string> = {
  google: "Google",
  microsoft: "Microsoft 365",
};

function ProfileForm({ user }: { user: AuthUser }): React.ReactElement {
  const qc = useQueryClient();
  const [name, setName] = useState(user.name ?? "");
  const [msg, setMsg] = useState<Msg>(null);

  const mut = useMutation({
    mutationFn: () => authApi.updateProfile({ name: name.trim() || null }),
    onSuccess: (updated) => {
      qc.setQueryData(["auth", "me"], updated);
      setMsg({ kind: "success", text: "Saved." });
    },
    onError: (err) =>
      setMsg({
        kind: "error",
        text: err instanceof ApiError ? err.detail : "Could not save.",
      }),
  });

  return (
    <SettingsSection
      title="Profile"
      description="Your display name is shown to teammates and in the activity log."
      onSubmit={() => {
        setMsg(null);
        mut.mutate();
      }}
      footerHint={<FormMessage message={msg} />}
      footer={
        <Button type="submit" size="sm" disabled={mut.isPending}>
          Save
        </Button>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pf-name">Display name</Label>
          <Input
            id="pf-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Optional"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pf-email">Email</Label>
          <Input id="pf-email" type="email" value={user.email} disabled />
        </div>
      </div>
    </SettingsSection>
  );
}

function PasswordForm({ user }: { user: AuthUser }): React.ReactElement {
  const qc = useQueryClient();
  const hasPw = user.has_password;
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState<Msg>(null);

  const mut = useMutation({
    mutationFn: () =>
      authApi.changePassword({
        current_password: hasPw ? current : null,
        new_password: next,
      }),
    onSuccess: (updated) => {
      qc.setQueryData(["auth", "me"], updated);
      setCurrent("");
      setNext("");
      setConfirm("");
      setMsg(null);
      toast.success(hasPw ? "Password updated." : "Password set.");
    },
    onError: (err) =>
      setMsg({
        kind: "error",
        text: err instanceof ApiError ? err.detail : "Could not save.",
      }),
  });

  function submit(): void {
    setMsg(null);
    if (next.length < 8) {
      setMsg({ kind: "error", text: "Password must be at least 8 characters." });
      return;
    }
    if (next !== confirm) {
      setMsg({ kind: "error", text: "Passwords don't match." });
      return;
    }
    mut.mutate();
  }

  return (
    <SettingsSection
      title={hasPw ? "Password" : "Set a password"}
      description={
        hasPw
          ? "Update the password you use to sign in."
          : "Add a password so you can sign in without a linked account."
      }
      onSubmit={submit}
      footerHint={
        msg ? <FormMessage message={msg} /> : "At least 8 characters."
      }
      footer={
        <Button type="submit" size="sm" disabled={mut.isPending}>
          {hasPw ? "Update password" : "Set password"}
        </Button>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        {hasPw ? (
          <div className="flex flex-col gap-1.5 sm:col-span-2 sm:max-w-[calc(50%-0.5rem)]">
            <Label htmlFor="pw-current">Current password</Label>
            <Input
              id="pw-current"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </div>
        ) : null}
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pw-new">New password</Label>
          <Input
            id="pw-new"
            type="password"
            autoComplete="new-password"
            minLength={8}
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pw-confirm">Confirm new password</Label>
          <Input
            id="pw-confirm"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </div>
      </div>
    </SettingsSection>
  );
}

function LinkedAccounts(): React.ReactElement {
  const q = useQuery({
    queryKey: ["identities"],
    queryFn: () => authApi.listIdentities(),
  });
  const identities: OAuthIdentitySummary[] = q.data ?? [];

  const linked = new Map(identities.map((i) => [i.provider, i]));

  return (
    <SettingsSection
      title="Sign-in methods"
      description="Third-party accounts you can use to sign in to Pulse."
      flush
    >
      {q.isPending ? (
        <p className="px-5 py-4 text-sm text-muted-foreground">Loading…</p>
      ) : (
        <ul className="divide-y divide-border">
          {Object.entries(PROVIDER_LABELS).map(([provider, label]) => {
            const id = linked.get(provider);
            return (
              <li key={provider} className="flex items-center gap-3 px-5 py-3">
                <span className="flex size-8 items-center justify-center rounded-md border border-border text-xs font-semibold text-muted-foreground">
                  {label[0]}
                </span>
                <span className="flex-1 text-sm font-medium text-foreground">
                  {label}
                </span>
                {id ? (
                  <span className="text-xs text-muted-foreground">
                    Connected {formatTimestamp(id.linked_at)}
                  </span>
                ) : (
                  <Pill>Not connected</Pill>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </SettingsSection>
  );
}

export function PersonalTab({
  user,
  orgName,
}: {
  user: AuthUser;
  orgName: string;
}): React.ReactElement {
  return (
    <div className="flex flex-col gap-5">
      <ProfileForm user={user} />
      <PasswordForm user={user} />
      <LinkedAccounts />
      <ApiKeysManager activeOrgName={orgName} />
    </div>
  );
}
