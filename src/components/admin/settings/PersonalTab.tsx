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
import { FormMessage, SettingsSection } from "./parts";

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
    <SettingsSection title="Profile" description="Your name and sign-in email.">
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setMsg(null);
          mut.mutate();
        }}
      >
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pf-email">Email</Label>
          <Input id="pf-email" type="email" value={user.email} disabled />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="pf-name">Display name</Label>
          <Input
            id="pf-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Optional"
          />
        </div>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={mut.isPending}>
            Save
          </Button>
          <FormMessage message={msg} />
        </div>
      </form>
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
      title={hasPw ? "Change password" : "Set a password"}
      description={
        hasPw
          ? "Update the password you use to sign in."
          : "Add a password so you can sign in without a linked account."
      }
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        {hasPw ? (
          <div className="flex flex-col gap-1.5">
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
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={mut.isPending}>
            {hasPw ? "Update password" : "Set password"}
          </Button>
          <FormMessage message={msg} />
        </div>
      </form>
    </SettingsSection>
  );
}

function LinkedAccounts(): React.ReactElement {
  const q = useQuery({
    queryKey: ["identities"],
    queryFn: () => authApi.listIdentities(),
  });
  const identities: OAuthIdentitySummary[] = q.data ?? [];

  return (
    <SettingsSection
      title="Linked accounts"
      description="Third-party accounts you can sign in with."
    >
      {q.isPending ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : identities.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No third-party accounts linked.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {identities.map((id) => (
            <li
              key={id.provider}
              className="flex items-center justify-between py-2.5 text-sm"
            >
              <span className="font-medium text-foreground">
                {PROVIDER_LABELS[id.provider] ?? id.provider}
              </span>
              <span className="text-xs text-muted-foreground">
                Linked {formatTimestamp(id.linked_at)}
              </span>
            </li>
          ))}
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
