import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Trash2, Upload } from "lucide-react";

import {
  ApiError,
  orgLogoUrl,
  orgsApi,
  type InviteSummary,
  type MemberRow,
  type OrgDetails,
} from "@/lib/api";
import { formatTimestamp } from "@/lib/format-time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { ConfirmDialog } from "../detail/EngagementDialogs";
import { BrandingSettings } from "./BrandingSettings";
import { FormMessage, SettingsSection } from "./parts";

type Msg = { kind: "success" | "error"; text: string } | null;

const MAX_LOGO_BYTES = 500 * 1024;

function OrgNameForm({
  org,
  isOwner,
}: {
  org: OrgDetails;
  isOwner: boolean;
}): React.ReactElement {
  const qc = useQueryClient();
  const [name, setName] = useState(org.name);
  const [msg, setMsg] = useState<Msg>(null);
  const mut = useMutation({
    mutationFn: () => orgsApi.updateMe({ name: name.trim() }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["orgs", "me"] });
      void qc.invalidateQueries({ queryKey: ["orgs", "mine"] });
      setMsg({ kind: "success", text: "Saved." });
    },
    onError: (err) =>
      setMsg({
        kind: "error",
        text: err instanceof ApiError ? err.detail : "Could not save.",
      }),
  });

  return (
    <SettingsSection title="Organization name">
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setMsg(null);
          mut.mutate();
        }}
      >
        <Input
          value={name}
          maxLength={200}
          disabled={!isOwner}
          onChange={(e) => setName(e.target.value)}
          aria-label="Organization name"
        />
        {isOwner ? (
          <div className="flex items-center gap-3">
            <Button type="submit" disabled={mut.isPending}>
              Save
            </Button>
            <FormMessage message={msg} />
          </div>
        ) : null}
      </form>
    </SettingsSection>
  );
}

function LogoSettings({ org }: { org: OrgDetails }): React.ReactElement {
  const qc = useQueryClient();
  const [removing, setRemoving] = useState(false);
  const logoUrl = orgLogoUrl(org.logo_path);
  const initial = (org.name[0] ?? "?").toUpperCase();

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["orgs", "me"] });
    void qc.invalidateQueries({ queryKey: ["orgs", "mine"] });
  };

  const uploadMut = useMutation({
    mutationFn: (file: File) => orgsApi.uploadLogo(file),
    onSuccess: () => {
      invalidate();
      toast.success("Logo updated.");
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.detail : "Could not upload."),
  });
  const deleteMut = useMutation({
    mutationFn: () => orgsApi.deleteLogo(),
    onSuccess: () => {
      setRemoving(false);
      invalidate();
      toast.success("Logo removed.");
    },
    onError: (err) => {
      setRemoving(false);
      toast.error(err instanceof ApiError ? err.detail : "Could not remove.");
    },
  });

  return (
    <SettingsSection
      title="Logo"
      description="Shown in the header and on client decks. PNG, JPG, SVG or WebP, up to 500 KB."
    >
      <div className="flex items-center gap-4">
        <span className="flex size-16 items-center justify-center overflow-hidden rounded-lg border border-border bg-muted text-xl font-semibold text-muted-foreground">
          {logoUrl ? (
            <img src={logoUrl} alt="" className="size-full object-contain" />
          ) : (
            initial
          )}
        </span>
        <div className="flex flex-wrap gap-2">
          <label className="inline-flex">
            <input
              type="file"
              accept="image/png,image/jpeg,image/svg+xml,image/webp"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (!file) return;
                if (file.size > MAX_LOGO_BYTES) {
                  toast.error("Logo must be 500 KB or smaller.");
                  return;
                }
                uploadMut.mutate(file);
              }}
            />
            <span className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-md border border-input px-3 text-sm font-semibold hover:bg-accent">
              <Upload className="size-4" />
              {logoUrl ? "Replace logo" : "Upload logo"}
            </span>
          </label>
          {logoUrl ? (
            <Button
              variant="ghost"
              onClick={() => setRemoving(true)}
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 />
              Remove
            </Button>
          ) : null}
        </div>
      </div>

      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title="Remove the logo?"
        description="The organization falls back to its initial until you upload a new one."
        confirmLabel="Remove logo"
        destructive
        pending={deleteMut.isPending}
        onConfirm={() => deleteMut.mutate()}
      />
    </SettingsSection>
  );
}

type MemberAction = { kind: "promote" | "demote" | "remove"; member: MemberRow };

function MembersSection({
  isOwner,
  currentUserId,
}: {
  isOwner: boolean;
  currentUserId: string;
}): React.ReactElement {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["members"],
    queryFn: () => orgsApi.listMembers(),
  });
  const members = q.data ?? [];
  const ownerCount = members.filter((m) => m.role === "owner").length;
  const [pending, setPending] = useState<MemberAction | null>(null);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["members"] });
    void qc.invalidateQueries({ queryKey: ["orgs", "me"] });
  };

  const mut = useMutation({
    mutationFn: async (a: MemberAction): Promise<void> => {
      if (a.kind === "remove") {
        await orgsApi.removeMember(a.member.user_id);
      } else {
        await orgsApi.updateMemberRole(
          a.member.user_id,
          a.kind === "promote" ? "owner" : "member",
        );
      }
    },
    onSuccess: (_data, a) => {
      setPending(null);
      invalidate();
      toast.success(a.kind === "remove" ? "Member removed." : "Role updated.");
    },
    onError: (err) => {
      setPending(null);
      toast.error(err instanceof ApiError ? err.detail : "Could not update.");
    },
  });

  const confirmCopy = (a: MemberAction) => {
    const who = a.member.name?.trim() || a.member.email;
    if (a.kind === "promote")
      return { title: "Make owner?", description: `${who} will get full owner access to this organization.`, label: "Make owner" };
    if (a.kind === "demote")
      return { title: "Demote to member?", description: `${who} will lose owner access.`, label: "Demote" };
    return { title: "Remove member?", description: `${who} will lose access to this organization.`, label: "Remove member" };
  };

  return (
    <SettingsSection title="Members">
      {q.isPending ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {members.map((m) => {
            const isSelf = m.user_id === currentUserId;
            const isLastOwner = m.role === "owner" && ownerCount <= 1;
            return (
              <li key={m.user_id} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-foreground">
                    {m.name?.trim() || m.email}
                    {isSelf ? (
                      <span className="ml-1 text-xs text-muted-foreground">
                        (you)
                      </span>
                    ) : null}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {m.email}
                  </div>
                </div>
                <Badge variant={m.role === "owner" ? "default" : "secondary"}>
                  {m.role}
                </Badge>
                {isOwner && !isLastOwner ? (
                  <div className="flex gap-1">
                    {m.role === "member" ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setPending({ kind: "promote", member: m })}
                      >
                        Make owner
                      </Button>
                    ) : (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setPending({ kind: "demote", member: m })}
                      >
                        Demote
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setPending({ kind: "remove", member: m })}
                      className="text-muted-foreground hover:text-destructive"
                    >
                      Remove
                    </Button>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {isOwner && ownerCount <= 1 ? (
        <p className="mt-3 text-xs text-muted-foreground">
          Promote another member before changing your own role.
        </p>
      ) : null}

      <ConfirmDialog
        open={pending !== null}
        onOpenChange={(o) => {
          if (!o) setPending(null);
        }}
        title={pending ? confirmCopy(pending).title : ""}
        description={pending ? confirmCopy(pending).description : ""}
        confirmLabel={pending ? confirmCopy(pending).label : "Confirm"}
        destructive={pending?.kind === "remove"}
        pending={mut.isPending}
        onConfirm={() => {
          if (pending) mut.mutate(pending);
        }}
      />
    </SettingsSection>
  );
}

function InvitesSection(): React.ReactElement {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [msg, setMsg] = useState<Msg>(null);
  const [revoking, setRevoking] = useState<InviteSummary | null>(null);

  const q = useQuery({
    queryKey: ["invites"],
    queryFn: () => orgsApi.listInvites(),
  });
  const invites = q.data ?? [];

  const inviteMut = useMutation({
    mutationFn: () => orgsApi.createInvite({ email: email.trim(), role }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["invites"] });
      void qc.invalidateQueries({ queryKey: ["orgs", "me"] });
      setMsg({ kind: "success", text: `Invite sent to ${email.trim()}.` });
      setEmail("");
    },
    onError: (err) =>
      setMsg({
        kind: "error",
        text: err instanceof ApiError ? err.detail : "Could not send invite.",
      }),
  });
  const revokeMut = useMutation({
    mutationFn: (id: string) => orgsApi.revokeInvite(id),
    onSuccess: () => {
      setRevoking(null);
      void qc.invalidateQueries({ queryKey: ["invites"] });
      void qc.invalidateQueries({ queryKey: ["orgs", "me"] });
      toast.success("Invite revoked.");
    },
    onError: (err) => {
      setRevoking(null);
      toast.error(err instanceof ApiError ? err.detail : "Could not revoke.");
    },
  });

  return (
    <SettingsSection
      title="Invite teammate"
      description="Invited people get an email link to join this organization."
    >
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setMsg(null);
          inviteMut.mutate();
        }}
      >
        <div className="flex min-w-48 flex-1 flex-col gap-1.5">
          <Label htmlFor="inv-email">Email</Label>
          <Input
            id="inv-email"
            type="email"
            required
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="inv-role">Role</Label>
          <Select value={role} onValueChange={setRole}>
            <SelectTrigger id="inv-role" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="member">Member</SelectItem>
              <SelectItem value="owner">Owner</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button type="submit" disabled={inviteMut.isPending}>
          Send invite
        </Button>
        <FormMessage message={msg} />
      </form>

      {invites.length > 0 ? (
        <ul className="mt-4 flex flex-col divide-y divide-border border-t border-border pt-2">
          {invites.map((inv) => (
            <li key={inv.id} className="flex items-center gap-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm font-medium text-foreground">
                  {inv.email}
                </div>
                <div className="text-xs text-muted-foreground">
                  {inv.role} · expires {formatTimestamp(inv.expires_at)}
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setRevoking(inv)}
                className="text-muted-foreground hover:text-destructive"
              >
                Revoke
              </Button>
            </li>
          ))}
        </ul>
      ) : null}

      <ConfirmDialog
        open={revoking !== null}
        onOpenChange={(o) => {
          if (!o) setRevoking(null);
        }}
        title="Revoke this invite?"
        description={`The invite link for ${revoking?.email ?? "this person"} stops working.`}
        confirmLabel="Revoke invite"
        destructive
        pending={revokeMut.isPending}
        onConfirm={() => {
          if (revoking) revokeMut.mutate(revoking.id);
        }}
      />
    </SettingsSection>
  );
}

export function OrganizationTab({
  org,
  currentUserId,
}: {
  org: OrgDetails;
  currentUserId: string;
}): React.ReactElement {
  const isOwner = org.role === "owner";
  return (
    <div className="flex flex-col gap-5">
      {!isOwner ? (
        <p className="rounded-lg border border-border bg-muted px-4 py-3 text-sm text-muted-foreground">
          Only owners can change organization settings.
        </p>
      ) : null}
      <OrgNameForm org={org} isOwner={isOwner} />
      {isOwner ? <LogoSettings org={org} /> : null}
      {isOwner ? (
        <div className="rounded-lg border border-border bg-card p-5">
          <BrandingSettings org={org} />
        </div>
      ) : null}
      <MembersSection isOwner={isOwner} currentUserId={currentUserId} />
      {isOwner ? <InvitesSection /> : null}
    </div>
  );
}
