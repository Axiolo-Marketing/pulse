import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Mail,
  MoreHorizontal,
  Send,
  ShieldCheck,
  ShieldOff,
  Trash2,
  Upload,
} from "lucide-react";

import {
  ApiError,
  orgLogoUrl,
  orgsApi,
  type InviteSummary,
  type MemberRow,
  type OrgDetails,
} from "@/lib/api";
import { formatTimestamp } from "@/lib/format-time";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { FormMessage, PersonAvatar, Pill, SettingsSection } from "./parts";

export function formatMemberCount(n: number): string {
  return `${n} member${n === 1 ? "" : "s"}`;
}

export function formatInviteCount(n: number): string {
  if (n === 0) return "no pending invites";
  return `${n} pending invite${n === 1 ? "" : "s"}`;
}

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
    <SettingsSection
      title="Organization name"
      description="Shown to your team and in emails sent to respondents."
      onSubmit={
        isOwner
          ? () => {
              setMsg(null);
              if (!name.trim()) {
                setMsg({ kind: "error", text: "Name is required." });
                return;
              }
              mut.mutate();
            }
          : undefined
      }
      footerHint={
        isOwner ? <FormMessage message={msg} /> : "Only owners can change this."
      }
      footer={
        isOwner ? (
          <Button type="submit" size="sm" disabled={mut.isPending}>
            Save
          </Button>
        ) : null
      }
    >
      <Input
        value={name}
        maxLength={200}
        disabled={!isOwner}
        onChange={(e) => setName(e.target.value)}
        aria-label="Organization name"
        className="sm:max-w-md"
      />
    </SettingsSection>
  );
}

function LogoSettings({
  org,
  isOwner = true,
}: {
  org: OrgDetails;
  isOwner?: boolean;
}): React.ReactElement {
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
      description="Shown to respondents at the top of their deck."
      footerHint={
        isOwner
          ? "PNG, JPG, SVG or WebP, up to 500 KB. Square works best."
          : "Only owners can change this."
      }
    >
      <div className="flex items-center gap-4">
        <span className="flex size-16 items-center justify-center overflow-hidden rounded-lg border border-border bg-muted text-xl font-semibold text-muted-foreground">
          {logoUrl ? (
            <img src={logoUrl} alt="" className="size-full object-contain" />
          ) : (
            initial
          )}
        </span>
        {isOwner ? (
        <div className="flex flex-wrap gap-2">
          <label className="inline-flex">
            <input
              type="file"
              accept="image/png,image/jpeg,image/svg+xml,image/webp"
              className="peer sr-only"
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
            <span className="inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-input bg-background px-3 text-sm font-medium shadow-xs hover:bg-accent peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50">
              <Upload className="size-4" />
              {logoUrl ? "Replace logo" : "Upload logo"}
            </span>
          </label>
          {logoUrl ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRemoving(true)}
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 />
              Remove
            </Button>
          ) : null}
        </div>
        ) : null}
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

function WebhookSettings({ org }: { org: OrgDetails }): React.ReactElement {
  const qc = useQueryClient();
  const [url, setUrl] = useState(org.webhook_url ?? "");
  const [secret, setSecret] = useState("");
  const [msg, setMsg] = useState<Msg>(null);
  const [clearing, setClearing] = useState(false);
  const isSet = Boolean(org.webhook_url && org.webhook_secret_set);

  const onDone = (text: string) => {
    void qc.invalidateQueries({ queryKey: ["orgs", "me"] });
    setSecret("");
    setMsg({ kind: "success", text });
  };
  const onFail = (err: unknown) =>
    setMsg({
      kind: "error",
      text: err instanceof ApiError ? err.detail : "Could not save.",
    });

  const saveMut = useMutation({
    mutationFn: () =>
      orgsApi.setWebhook({
        url: url.trim(),
        ...(secret.trim() ? { secret: secret.trim() } : {}),
      }),
    onSuccess: () => onDone("Saved."),
    onError: onFail,
  });
  const clearMut = useMutation({
    mutationFn: () => orgsApi.clearWebhook(),
    onSuccess: () => {
      setClearing(false);
      setUrl("");
      onDone("Webhook cleared.");
    },
    onError: (err) => {
      setClearing(false);
      onFail(err);
    },
  });

  return (
    <SettingsSection
      title="Outbound webhook"
      description="Pulse posts a signed event here when a respondent opens their deck, answers a card, shares a contact, or finishes."
      action={<Pill tone={isSet ? "strong" : "default"}>{isSet ? "Set" : "Not set"}</Pill>}
      onSubmit={() => {
        setMsg(null);
        if (!url.trim().startsWith("https://")) {
          setMsg({ kind: "error", text: "The URL must start with https://." });
          return;
        }
        if (!org.webhook_secret_set && !secret.trim()) {
          setMsg({ kind: "error", text: "Paste the signing secret." });
          return;
        }
        saveMut.mutate();
      }}
      footerHint={<FormMessage message={msg} />}
      footer={
        <>
          {org.webhook_url || org.webhook_secret_set ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setClearing(true)}
              className="text-muted-foreground hover:text-destructive"
            >
              Clear
            </Button>
          ) : null}
          <Button type="submit" size="sm" disabled={saveMut.isPending}>
            Save
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4 sm:max-w-md">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="webhook-url">URL</Label>
          <Input
            id="webhook-url"
            type="url"
            placeholder="https://"
            value={url}
            maxLength={2000}
            onChange={(e) => setUrl(e.target.value)}
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="webhook-secret">Signing secret</Label>
          <Input
            id="webhook-secret"
            type="password"
            autoComplete="off"
            placeholder={
              org.webhook_secret_set ? "Set. Paste a new one to replace it." : "Not set"
            }
            value={secret}
            maxLength={500}
            onChange={(e) => setSecret(e.target.value)}
          />
        </div>
      </div>

      <ConfirmDialog
        open={clearing}
        onOpenChange={setClearing}
        title="Clear the webhook?"
        description="Pulse stops sending respondent events until you set it again."
        confirmLabel="Clear webhook"
        destructive
        pending={clearMut.isPending}
        onConfirm={() => clearMut.mutate()}
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
    <SettingsSection
      title="Members"
      description="People who can sign in and manage engagements."
      flush
      footerHint={
        isOwner && ownerCount <= 1
          ? "Promote another member before changing your own role."
          : undefined
      }
    >
      {q.isPending ? (
        <p className="px-5 py-4 text-sm text-muted-foreground">Loading…</p>
      ) : (
        <ul className="divide-y divide-border">
          {members.map((m) => {
            const isSelf = m.user_id === currentUserId;
            const isLastOwner = m.role === "owner" && ownerCount <= 1;
            const display = m.name?.trim() || m.email;
            return (
              <li key={m.user_id} className="flex items-center gap-3 px-5 py-3">
                <PersonAvatar label={display} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-foreground">
                    {display}
                    {isSelf ? (
                      <span className="ml-1.5 font-normal text-muted-foreground">
                        (you)
                      </span>
                    ) : null}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {m.email}
                  </div>
                </div>
                <Pill tone={m.role === "owner" ? "strong" : "default"}>
                  {m.role === "owner" ? "Owner" : "Member"}
                </Pill>
                {isOwner ? (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="-mr-2 size-8 text-muted-foreground hover:text-foreground"
                        disabled={isLastOwner}
                        aria-label={`Actions for ${display}`}
                        title={isLastOwner ? "The last owner can't be changed" : undefined}
                      >
                        <MoreHorizontal />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="min-w-44">
                      {m.role === "member" ? (
                        <DropdownMenuItem
                          onSelect={() => setPending({ kind: "promote", member: m })}
                        >
                          <ShieldCheck />
                          Make owner
                        </DropdownMenuItem>
                      ) : (
                        <DropdownMenuItem
                          onSelect={() => setPending({ kind: "demote", member: m })}
                        >
                          <ShieldOff />
                          Demote to member
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => setPending({ kind: "remove", member: m })}
                      >
                        <Trash2 />
                        Remove
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}

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
      title="Invite teammates"
      description="Invited people get an email link to join this organization."
      onSubmit={() => {
        setMsg(null);
        inviteMut.mutate();
      }}
      footerHint={<FormMessage message={msg} />}
      footer={
        <Button type="submit" size="sm" disabled={inviteMut.isPending}>
          <Send />
          Send invite
        </Button>
      }
    >
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex min-w-48 flex-1 flex-col gap-1.5">
          <Label htmlFor="inv-email">Email</Label>
          <Input
            id="inv-email"
            type="email"
            required
            autoComplete="off"
            placeholder="teammate@company.com"
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
      </div>

      {invites.length > 0 ? (
        <div className="mt-5">
          <h3 className="mb-1 text-xs font-medium text-muted-foreground">
            Pending invites
          </h3>
          <ul className="divide-y divide-border rounded-md border border-border">
            {invites.map((inv) => (
              <li key={inv.id} className="flex items-center gap-3 px-3 py-2.5">
                <Mail className="size-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-foreground">
                    {inv.email}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    Invited {formatTimestamp(inv.created_at)}
                    {inv.invited_by_email ? ` by ${inv.invited_by_email}` : ""}
                    {" · "}expires {formatTimestamp(inv.expires_at)}
                  </div>
                </div>
                <Pill>{inv.role === "owner" ? "Owner" : "Member"}</Pill>
                <Button
                  type="button"
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
        </div>
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
    <div className="flex flex-col gap-6">
      {!isOwner ? (
        <p className="rounded-lg border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
          Only owners can change organization settings.
        </p>
      ) : null}
      <p className="text-sm text-muted-foreground" data-testid="org-counts">
        {formatMemberCount(org.member_count)} ·{" "}
        {formatInviteCount(org.pending_invite_count)}
      </p>
      <OrgNameForm org={org} isOwner={isOwner} />
      <LogoSettings org={org} isOwner={isOwner} />
      {isOwner ? <BrandingSettings org={org} /> : null}
      {isOwner ? <WebhookSettings org={org} /> : null}
      <MembersSection isOwner={isOwner} currentUserId={currentUserId} />
      {isOwner ? <InvitesSection /> : null}
    </div>
  );
}
