import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MoreHorizontal, Plus, Trash2, Users } from "lucide-react";
import { toast } from "sonner";

import {
  superadminApi,
  ApiError,
  type ReactiveUsageEngagementRow,
  type ReactiveUsageMonthlyRow,
  type SuperadminMemberRow,
  type SuperadminOrgRow,
} from "@/lib/api";
import { formatTimestamp } from "@/lib/format-time";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { cn } from "@/lib/utils";

import { Field } from "../form-parts";
import { PersonAvatar, Pill, SettingsSection } from "../settings/parts";

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface FieldErrors {
  name?: string;
  slug?: string;
  ownerEmail?: string;
}

function validate(
  name: string,
  slug: string,
  ownerEmail: string,
  inviteOwner: boolean,
): FieldErrors {
  const errs: FieldErrors = {};

  const n = name.trim();
  if (!n) errs.name = "Organization name is required.";
  else if (n.length > 200) errs.name = "Name must be 200 characters or fewer.";

  const s = slug.trim();
  if (!s) errs.slug = "Slug is required.";
  else if (s.length < 2 || s.length > 40)
    errs.slug = "Slug must be 2–40 characters.";
  else if (!SLUG_RE.test(s))
    errs.slug = "Use lowercase letters, numbers, and single hyphens.";

  if (inviteOwner) {
    const e = ownerEmail.trim();
    if (!e) errs.ownerEmail = "Owner email is required.";
    else if (!EMAIL_RE.test(e)) errs.ownerEmail = "Enter a valid email address.";
  }

  return errs;
}

function FieldError({
  message,
}: {
  message?: string;
}): React.ReactElement | null {
  if (!message) return null;
  return (
    <p className="text-xs text-destructive" role="alert">
      {message}
    </p>
  );
}

/** Lowercase, hyphenated slug suggestion from an org name. */
function slugify(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/g, "");
}

function CreateOrgDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  // Slug follows the name until the operator edits it by hand.
  const [slugTouched, setSlugTouched] = useState(false);
  const [ownerEmail, setOwnerEmail] = useState("");
  const [inviteOwner, setInviteOwner] = useState(true);
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [apiError, setApiError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName("");
      setSlug("");
      setSlugTouched(false);
      setOwnerEmail("");
      setInviteOwner(true);
      setFieldErrors({});
      setApiError(null);
    }
  }, [open]);

  const mut = useMutation({
    mutationFn: (args: { name: string; slug: string; owner_email?: string }) =>
      superadminApi.createOrg(args),
    onSuccess: (res) => {
      void qc.invalidateQueries({ queryKey: ["superadmin", "orgs"] });
      // You may now own it — refresh the org switcher.
      void qc.invalidateQueries({ queryKey: ["orgs", "mine"] });
      toast.success(
        "Organization created.",
        res.invite
          ? { description: `Invite emailed to ${res.invite.email}.` }
          : { description: "You're its owner. Switch to it from the org menu." },
      );
      onOpenChange(false);
    },
    onError: (err) =>
      setApiError(
        err instanceof ApiError ? err.detail : "Could not create organization.",
      ),
  });

  function submit(): void {
    setApiError(null);
    const errs = validate(name, slug, ownerEmail, inviteOwner);
    setFieldErrors(errs);
    if (Object.keys(errs).length > 0) return;
    mut.mutate({
      name: name.trim(),
      slug: slug.trim(),
      ...(inviteOwner ? { owner_email: ownerEmail.trim() } : {}),
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New organization</DialogTitle>
          <DialogDescription>
            A separate space with its own engagements, branding and people.
          </DialogDescription>
        </DialogHeader>
        <form
          id="create-org-form"
          className="flex flex-col gap-4"
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <Field id="so-name" label="Organization name">
            <Input
              id="so-name"
              value={name}
              maxLength={200}
              autoFocus
              onChange={(e) => {
                setName(e.target.value);
                if (!slugTouched) setSlug(slugify(e.target.value));
                setApiError(null);
              }}
              aria-invalid={fieldErrors.name ? true : undefined}
              placeholder="Acme, Inc."
            />
            <FieldError message={fieldErrors.name} />
          </Field>
          <Field
            id="so-slug"
            label="Slug"
            hint={
              fieldErrors.slug
                ? undefined
                : "Lowercase letters, numbers and hyphens."
            }
          >
            <Input
              id="so-slug"
              value={slug}
              maxLength={40}
              onChange={(e) => {
                setSlug(e.target.value);
                setSlugTouched(true);
                setApiError(null);
              }}
              aria-invalid={fieldErrors.slug ? true : undefined}
              placeholder="acme"
              className="font-mono text-sm"
            />
            <FieldError message={fieldErrors.slug} />
          </Field>
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-2 text-sm font-medium text-foreground">
              Who manages it?
            </legend>
            {(
              [
                {
                  value: true,
                  title: "Invite an owner by email",
                  body: "Emails them a join link. Once accepted they get their own login to this organization's admin.",
                },
                {
                  value: false,
                  title: "Just me (no email)",
                  body: "You're the owner. Nobody else is invited or emailed.",
                },
              ] as const
            ).map((opt) => (
              <label
                key={String(opt.value)}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded-lg border px-3.5 py-3 transition-colors",
                  inviteOwner === opt.value
                    ? "border-foreground bg-muted/40"
                    : "border-border hover:bg-muted/30",
                )}
              >
                <input
                  type="radio"
                  name="org-owner"
                  className="mt-1 accent-foreground"
                  checked={inviteOwner === opt.value}
                  onChange={() => {
                    setInviteOwner(opt.value);
                    setFieldErrors((f) => ({ ...f, ownerEmail: undefined }));
                  }}
                />
                <span>
                  <span className="block text-sm font-medium text-foreground">
                    {opt.title}
                  </span>
                  <span className="block text-xs text-muted-foreground">{opt.body}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {inviteOwner ? (
            <Field id="so-owner" label="Owner email">
              <Input
                id="so-owner"
                type="email"
                value={ownerEmail}
                onChange={(e) => {
                  setOwnerEmail(e.target.value);
                  setApiError(null);
                }}
                aria-invalid={fieldErrors.ownerEmail ? true : undefined}
                placeholder="owner@acme.com"
              />
              <FieldError message={fieldErrors.ownerEmail} />
            </Field>
          ) : null}
          {apiError ? (
            <p className="text-sm text-destructive" role="alert">
              {apiError}
            </p>
          ) : null}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="create-org-form" disabled={mut.isPending}>
            Create organization
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MembersDialog({
  org,
  onOpenChange,
}: {
  org: SuperadminOrgRow | null;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const open = org !== null;
  const membersQ = useQuery({
    queryKey: ["superadmin", "orgMembers", org?.id ?? ""],
    queryFn: () => superadminApi.listOrgMembers(org!.id),
    enabled: open,
  });

  const members: SuperadminMemberRow[] = membersQ.data ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Members of {org?.name}</DialogTitle>
          <DialogDescription>
            Read-only list of everyone in this organization.
          </DialogDescription>
        </DialogHeader>
        {membersQ.isPending ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-11 w-full" />
            ))}
          </div>
        ) : membersQ.isError ? (
          <p className="py-2 text-sm text-destructive" role="alert">
            Couldn't load members.
          </p>
        ) : members.length === 0 ? (
          <p className="py-2 text-sm text-muted-foreground">No members yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-border">
            {members.map((m) => (
              <li key={m.user_id} className="flex items-center gap-3 py-2.5">
                <PersonAvatar label={m.name || m.email} />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-foreground">
                    {m.name || m.email}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {m.email}
                  </div>
                </div>
                <Pill tone={m.role === "owner" ? "strong" : "default"}>
                  {m.role === "owner" ? "Owner" : "Member"}
                </Pill>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Delete an organization. Shows exactly what will be erased; an org with
 * any data must be confirmed by typing its name (the server enforces the
 * same rule). */
function DeleteOrgDialog({
  org,
  onOpenChange,
}: {
  org: SuperadminOrgRow | null;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const qc = useQueryClient();
  const [typed, setTyped] = useState("");
  useEffect(() => setTyped(""), [org?.id]);

  const impactQ = useQuery({
    queryKey: ["superadmin", "delete-impact", org?.id],
    queryFn: () => superadminApi.orgDeleteImpact(org!.id),
    enabled: org !== null,
  });
  const impact = impactQ.data;
  const hasData =
    !!impact &&
    (impact.clients > 0 ||
      impact.engagements > 0 ||
      impact.api_keys > 0 ||
      impact.members > 1);
  const nameMatches =
    !!org && typed.trim().toLowerCase() === org.name.trim().toLowerCase();

  const mut = useMutation({
    mutationFn: () =>
      superadminApi.deleteOrg(org!.id, hasData ? typed.trim() : undefined),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["superadmin"] });
      void qc.invalidateQueries({ queryKey: ["orgs", "mine"] });
      toast.success(`${org?.name} deleted.`);
      onOpenChange(false);
    },
    onError: (err) => {
      // The server says why (e.g. the name didn't match) — show it.
      toast.error("Couldn't delete that organization.", {
        description: err instanceof ApiError ? err.detail : undefined,
      });
    },
  });

  const rows: [string, number][] = impact
    ? [
        ["Clients", impact.clients],
        ["Engagements", impact.engagements],
        ["Questions", impact.questions],
        ["Respondents", impact.respondents],
        ["Answers", impact.answers],
        ["Uploaded files", impact.files],
        ["Members", impact.members],
        ["Pending invites", impact.pending_invites],
        ["API keys", impact.api_keys],
      ]
    : [];

  return (
    <Dialog open={org !== null} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {org?.name}?</DialogTitle>
          <DialogDescription>
            This permanently erases the organization and everything in it.
            Member accounts are kept, but lose access. It can't be undone;
            only a server backup could bring it back.
          </DialogDescription>
        </DialogHeader>
        {impactQ.isPending ? (
          <Skeleton className="h-40 w-full" />
        ) : impactQ.isError ? (
          <p className="text-sm text-destructive" role="alert">
            Couldn't load what this would delete. Close and try again.
          </p>
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 rounded-lg border border-border px-4 py-3 text-sm sm:grid-cols-3">
              {rows.map(([label, n]) => (
                <div key={label} className="flex items-baseline justify-between gap-2">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd
                    className={cn(
                      "tabular-nums",
                      n > 0 ? "font-semibold text-foreground" : "text-muted-foreground",
                    )}
                  >
                    {n}
                  </dd>
                </div>
              ))}
            </dl>
            {hasData ? (
              <Field
                id="delete-org-confirm"
                label={`Type ${org?.name} to confirm`}
              >
                <Input
                  id="delete-org-confirm"
                  value={typed}
                  autoComplete="off"
                  onChange={(e) => setTyped(e.target.value)}
                  placeholder={org?.name}
                />
              </Field>
            ) : (
              <p className="text-sm text-muted-foreground">
                This organization is empty.
              </p>
            )}
          </>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={!impact || (hasData && !nameMatches) || mut.isPending}
            onClick={() => mut.mutate()}
          >
            <Trash2 />
            Delete organization
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function OrgsTableSection(): React.ReactElement {
  const qc = useQueryClient();
  const orgsQ = useQuery({
    queryKey: ["superadmin", "orgs"],
    queryFn: () => superadminApi.listOrgs({ limit: 100 }),
  });

  const [membersOrg, setMembersOrg] = useState<SuperadminOrgRow | null>(null);
  const [deletingOrg, setDeletingOrg] = useState<SuperadminOrgRow | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const reactiveMut = useMutation({
    mutationFn: (args: { org: SuperadminOrgRow; allowed: boolean }) =>
      superadminApi.updateOrgFlags(args.org.id, {
        reactive_cards_allowed: args.allowed,
      }),
    onSuccess: (_data, args) => {
      void qc.invalidateQueries({ queryKey: ["superadmin", "orgs"] });
      toast.success(
        `AI follow-ups ${args.allowed ? "enabled" : "disabled"} for ${args.org.name}.`,
      );
    },
    onError: (_err, args) => {
      toast.error(`Couldn't update AI follow-ups for ${args.org.name}.`);
    },
  });

  const orgs = orgsQ.data ?? [];

  return (
    <SettingsSection
      title="Organizations"
      description="Every tenant on this Pulse deployment."
      action={
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus />
          New organization
        </Button>
      }
      flush
    >
      <Table>
        <TableHeader className="bg-muted/40">
          <TableRow>
            <TableHead className="pl-5">Organization</TableHead>
            <TableHead className="text-right">Members</TableHead>
            <TableHead className="hidden text-right sm:table-cell">
              Invites
            </TableHead>
            <TableHead className="hidden md:table-cell">Owners</TableHead>
            <TableHead>AI follow-ups</TableHead>
            <TableHead className="hidden lg:table-cell">Created</TableHead>
            <TableHead className="w-0 pr-5">
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {orgsQ.isPending ? (
            Array.from({ length: 4 }).map((_, i) => (
              <TableRow key={i}>
                <TableCell colSpan={7} className="px-5">
                  <Skeleton className="h-8 w-full" />
                </TableCell>
              </TableRow>
            ))
          ) : orgsQ.isError ? (
            <TableRow>
              <TableCell
                colSpan={7}
                className="py-10 text-center text-sm text-destructive"
              >
                Couldn't load organizations.
              </TableCell>
            </TableRow>
          ) : orgs.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={7}
                className="py-10 text-center text-sm text-muted-foreground"
              >
                No organizations yet.
              </TableCell>
            </TableRow>
          ) : (
            orgs.map((org) => {
              return (
                <TableRow key={org.id}>
                  <TableCell className="pl-5">
                    <div className="flex items-center gap-3">
                      <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-xs font-semibold text-muted-foreground">
                        {(org.name[0] ?? "?").toUpperCase()}
                      </span>
                      <div className="min-w-0">
                        <div className="truncate font-medium text-foreground">
                          {org.name}
                        </div>
                        <div className="font-mono text-xs text-muted-foreground">
                          {org.slug}
                        </div>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {org.member_count}
                  </TableCell>
                  <TableCell className="hidden text-right tabular-nums text-muted-foreground sm:table-cell">
                    {org.pending_invite_count}
                  </TableCell>
                  <TableCell className="hidden max-w-56 truncate text-muted-foreground md:table-cell">
                    {org.owner_emails.length > 0
                      ? org.owner_emails.join(", ")
                      : "—"}
                  </TableCell>
                  <TableCell>
                    <Switch
                      checked={org.reactive_cards_allowed}
                      disabled={
                        reactiveMut.isPending &&
                        reactiveMut.variables?.org.id === org.id
                      }
                      onCheckedChange={(allowed) =>
                        reactiveMut.mutate({ org, allowed })
                      }
                      aria-label={`AI follow-ups for ${org.name}`}
                    />
                  </TableCell>
                  <TableCell className="hidden whitespace-nowrap text-muted-foreground lg:table-cell">
                    {formatTimestamp(org.created_at)}
                  </TableCell>
                  <TableCell className="pr-5">
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-8 text-muted-foreground hover:text-foreground"
                          aria-label={`Actions for ${org.name}`}
                        >
                          <MoreHorizontal />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="min-w-52">
                        <DropdownMenuItem onSelect={() => setMembersOrg(org)}>
                          <Users />
                          View members
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          variant="destructive"
                          onSelect={() => setDeletingOrg(org)}
                        >
                          <Trash2 />
                          Delete organization…
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>

      <CreateOrgDialog open={createOpen} onOpenChange={setCreateOpen} />

      <MembersDialog
        org={membersOrg}
        onOpenChange={(o) => {
          if (!o) setMembersOrg(null);
        }}
      />

      <DeleteOrgDialog
        org={deletingOrg}
        onOpenChange={(o) => {
          if (!o) setDeletingOrg(null);
        }}
      />
    </SettingsSection>
  );
}

const USAGE_WINDOWS = [30, 90] as const;
type UsageWindow = (typeof USAGE_WINDOWS)[number];

function formatTokens(n: number): string {
  return n.toLocaleString();
}

function formatCost(n: number): string {
  return `$${n.toFixed(4)}`;
}

function EngagementUsageTable({
  engagements,
  days,
  isPending,
  isError,
}: {
  engagements: ReactiveUsageEngagementRow[];
  days: number;
  isPending: boolean;
  isError: boolean;
}): React.ReactElement {
  return (
    <div className="border-t border-border">
      <div className="px-5 pt-5 pb-3">
        <h3 className="text-sm font-semibold text-foreground">By engagement</h3>
        <p className="mt-0.5 text-sm text-muted-foreground">
          Same window, per engagement. Only engagements with at least one
          generation appear.
        </p>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Engagement</TableHead>
            <TableHead>Organization</TableHead>
            <TableHead className="text-right">Calls</TableHead>
            <TableHead className="text-right">Tokens (in / out)</TableHead>
            <TableHead className="text-right">Est. cost</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isPending ? (
            Array.from({ length: 3 }).map((_, i) => (
              <TableRow key={i}>
                {Array.from({ length: 5 }).map((__, j) => (
                  <TableCell key={j}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : isError ? (
            <TableRow>
              <TableCell
                colSpan={5}
                className="py-10 text-center text-sm text-destructive"
              >
                Couldn't load engagement usage.
              </TableCell>
            </TableRow>
          ) : engagements.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={5}
                className="py-10 text-center text-sm text-muted-foreground"
              >
                No engagements with AI follow-up activity in the last {days}{" "}
                days.
              </TableCell>
            </TableRow>
          ) : (
            engagements.map((e) => (
              <TableRow key={e.engagement_id}>
                <TableCell className="font-medium text-foreground">
                  {e.engagement_label}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {e.org_name}
                </TableCell>
                <TableCell className="text-right">{e.generations}</TableCell>
                <TableCell className="text-right text-muted-foreground">
                  {formatTokens(e.input_tokens)} /{" "}
                  {formatTokens(e.output_tokens)}
                </TableCell>
                <TableCell className="text-right">
                  {formatCost(e.cost_usd)}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

function MonthlyUsagePanel({
  monthly,
  isPending,
  isError,
}: {
  monthly: ReactiveUsageMonthlyRow[];
  isPending: boolean;
  isError: boolean;
}): React.ReactElement {
  return (
    <SettingsSection
      title="Monthly cost by organization"
      description="Trailing 6 calendar months, most recent first. Not affected by the window above."
      flush
      className={
        "[&_td:first-child]:pl-5 [&_th:first-child]:pl-5 [&_td:last-child]:pr-5 [&_th:last-child]:pr-5 [&_thead]:bg-muted/40"
      }
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Month</TableHead>
            <TableHead>Organization</TableHead>
            <TableHead className="text-right">Calls</TableHead>
            <TableHead className="text-right">Tokens (in / out)</TableHead>
            <TableHead className="text-right">Est. cost</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {isPending ? (
            Array.from({ length: 3 }).map((_, i) => (
              <TableRow key={i}>
                {Array.from({ length: 5 }).map((__, j) => (
                  <TableCell key={j}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : isError ? (
            <TableRow>
              <TableCell
                colSpan={5}
                className="py-10 text-center text-sm text-destructive"
              >
                Couldn't load monthly usage.
              </TableCell>
            </TableRow>
          ) : monthly.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={5}
                className="py-10 text-center text-sm text-muted-foreground"
              >
                No AI follow-up activity in the last 6 months.
              </TableCell>
            </TableRow>
          ) : (
            monthly.map((m) => (
              <TableRow key={`${m.month}-${m.org_id}`}>
                <TableCell className="font-medium text-foreground">
                  {m.month}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {m.org_name}
                </TableCell>
                <TableCell className="text-right">{m.generations}</TableCell>
                <TableCell className="text-right text-muted-foreground">
                  {formatTokens(m.input_tokens)} /{" "}
                  {formatTokens(m.output_tokens)}
                </TableCell>
                <TableCell className="text-right">
                  {formatCost(m.cost_usd)}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </SettingsSection>
  );
}

function ReactiveUsagePanel(): React.ReactElement {
  const [days, setDays] = useState<UsageWindow>(30);
  const usageQ = useQuery({
    queryKey: ["superadmin", "reactiveUsage", days],
    queryFn: () => superadminApi.reactiveUsage({ days }),
  });

  const orgs = usageQ.data?.orgs ?? [];
  const totals = usageQ.data?.totals;
  const engagements = usageQ.data?.engagements ?? [];
  const monthly = usageQ.data?.monthly ?? [];

  return (
    <>
      <SettingsSection
        title="AI follow-up usage"
        description="LLM calls, tokens and estimated cost per organization. For monitoring, not billing."
        action={
          <div
            role="radiogroup"
            aria-label="Time window"
            className="inline-flex rounded-md border border-border p-0.5"
          >
            {USAGE_WINDOWS.map((w) => (
              <button
                key={w}
                type="button"
                role="radio"
                aria-checked={days === w}
                onClick={() => setDays(w)}
                className={cn(
                  "rounded-[5px] px-2.5 py-1 text-xs font-medium transition-colors",
                  days === w
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {w} days
              </button>
            ))}
          </div>
        }
        flush
        className={
          "[&_td:first-child]:pl-5 [&_th:first-child]:pl-5 [&_td:last-child]:pr-5 [&_th:last-child]:pr-5 [&_thead]:bg-muted/40"
        }
      >
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Organization</TableHead>
              <TableHead className="text-right">Calls</TableHead>
              <TableHead className="text-right">Completed</TableHead>
              <TableHead className="text-right">Skipped</TableHead>
              <TableHead className="text-right">Failed</TableHead>
              <TableHead className="text-right">Tokens (in / out)</TableHead>
              <TableHead className="text-right">Est. cost</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {usageQ.isPending ? (
              Array.from({ length: 3 }).map((_, i) => (
                <TableRow key={i}>
                  {Array.from({ length: 7 }).map((__, j) => (
                    <TableCell key={j}>
                      <Skeleton className="h-5 w-full" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : usageQ.isError ? (
              <TableRow>
                <TableCell
                  colSpan={7}
                  className="py-10 text-center text-sm text-destructive"
                >
                  Couldn't load AI follow-up usage.
                </TableCell>
              </TableRow>
            ) : orgs.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={7}
                  className="py-10 text-center text-sm text-muted-foreground"
                >
                  No AI follow-up activity in the last {days} days.
                </TableCell>
              </TableRow>
            ) : (
              <>
                {orgs.map((o) => (
                  <TableRow key={o.org_id}>
                    <TableCell className="font-medium text-foreground">
                      {o.org_name}
                    </TableCell>
                    <TableCell className="text-right">
                      {o.generations}
                    </TableCell>
                    <TableCell className="text-right">{o.completed}</TableCell>
                    <TableCell className="text-right">{o.skipped}</TableCell>
                    <TableCell className="text-right">{o.failed}</TableCell>
                    <TableCell className="text-right text-muted-foreground">
                      {formatTokens(o.input_tokens)} /{" "}
                      {formatTokens(o.output_tokens)}
                    </TableCell>
                    <TableCell className="text-right">
                      {formatCost(o.cost_usd)}
                    </TableCell>
                  </TableRow>
                ))}
                {totals ? (
                  <TableRow className="bg-muted/40 font-medium">
                    <TableCell>All organizations</TableCell>
                    <TableCell className="text-right">
                      {totals.generations}
                    </TableCell>
                    <TableCell className="text-right">
                      {totals.completed}
                    </TableCell>
                    <TableCell className="text-right">
                      {totals.skipped}
                    </TableCell>
                    <TableCell className="text-right">
                      {totals.failed}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground">
                      {formatTokens(totals.input_tokens)} /{" "}
                      {formatTokens(totals.output_tokens)}
                    </TableCell>
                    <TableCell className="text-right">
                      {formatCost(totals.cost_usd)}
                    </TableCell>
                  </TableRow>
                ) : null}
              </>
            )}
          </TableBody>
        </Table>
        <EngagementUsageTable
          engagements={engagements}
          days={days}
          isPending={usageQ.isPending}
          isError={usageQ.isError}
        />
      </SettingsSection>
      <MonthlyUsagePanel
        monthly={monthly}
        isPending={usageQ.isPending}
        isError={usageQ.isError}
      />
    </>
  );
}

function SummaryStats(): React.ReactElement {
  // Same query keys as the sections below, so these are shared, not refetched.
  const orgsQ = useQuery({
    queryKey: ["superadmin", "orgs"],
    queryFn: () => superadminApi.listOrgs({ limit: 100 }),
  });
  const usageQ = useQuery({
    queryKey: ["superadmin", "reactiveUsage", 30],
    queryFn: () => superadminApi.reactiveUsage({ days: 30 }),
  });
  const orgs = orgsQ.data ?? [];
  const sum = (f: (o: SuperadminOrgRow) => number): number =>
    orgs.reduce((n, o) => n + f(o), 0);
  const stats: { label: string; value: string }[] = [
    { label: "Organizations", value: orgsQ.data ? String(orgs.length) : "—" },
    {
      label: "Members",
      value: orgsQ.data ? String(sum((o) => o.member_count)) : "—",
    },
    {
      label: "Pending invites",
      value: orgsQ.data ? String(sum((o) => o.pending_invite_count)) : "—",
    },
    {
      label: "AI cost, last 30 days",
      value: usageQ.data?.totals
        ? formatCost(usageQ.data.totals.cost_usd)
        : "—",
    },
  ];
  return (
    <div className="mb-8 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-4">
      {stats.map((st) => (
        <div key={st.label} className="bg-background px-4 py-3">
          <div className="text-xs text-muted-foreground">{st.label}</div>
          <div className="mt-1 text-lg font-semibold tabular-nums tracking-tight text-foreground">
            {st.value}
          </div>
        </div>
      ))}
    </div>
  );
}

export function SuperadminPage(): React.ReactElement {
  return (
    <main className="mx-auto w-full max-w-6xl px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          Superadmin
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Organizations and usage across the whole deployment.
        </p>
      </div>
      <SummaryStats />
      <div className="flex flex-col gap-6">
        <OrgsTableSection />
        <ReactiveUsagePanel />
      </div>
    </main>
  );
}
