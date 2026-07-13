import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, Users } from "lucide-react";
import { toast } from "sonner";

import {
  superadminApi,
  ApiError,
  type SuperadminMemberRow,
  type SuperadminOrgRow,
} from "@/lib/api";
import { formatTimestamp } from "@/lib/format-time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

import { ConfirmDialog } from "../detail/EngagementDialogs";

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface FieldErrors {
  name?: string;
  slug?: string;
  ownerEmail?: string;
}

function validate(name: string, slug: string, ownerEmail: string): FieldErrors {
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

  const e = ownerEmail.trim();
  if (!e) errs.ownerEmail = "Owner email is required.";
  else if (!EMAIL_RE.test(e)) errs.ownerEmail = "Enter a valid email address.";

  return errs;
}

function FieldError({ message }: { message?: string }): React.ReactElement | null {
  if (!message) return null;
  return (
    <p className="text-xs text-destructive" role="alert">
      {message}
    </p>
  );
}

function CreateOrgCard(): React.ReactElement {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [ownerEmail, setOwnerEmail] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [apiError, setApiError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const mut = useMutation({
    mutationFn: (args: { name: string; slug: string; owner_email: string }) =>
      superadminApi.createOrg(args),
    onSuccess: (res) => {
      setSuccess(`Invite sent to ${res.invite.email}`);
      setName("");
      setSlug("");
      setOwnerEmail("");
      setFieldErrors({});
      void qc.invalidateQueries({ queryKey: ["superadmin", "orgs"] });
      toast.success("Organization created.");
    },
    onError: (err) =>
      setApiError(
        err instanceof ApiError ? err.detail : "Could not create organization.",
      ),
  });

  // Clearing stale feedback the moment the operator edits keeps the success
  // banner from lingering over a form they've started changing again.
  function clearFeedback(): void {
    if (success) setSuccess(null);
    if (apiError) setApiError(null);
  }

  function submit(): void {
    setApiError(null);
    setSuccess(null);
    const errs = validate(name, slug, ownerEmail);
    setFieldErrors(errs);
    if (Object.keys(errs).length > 0) return;
    mut.mutate({
      name: name.trim(),
      slug: slug.trim(),
      owner_email: ownerEmail.trim(),
    });
  }

  return (
    <section className="mb-8 rounded-lg border border-border bg-card p-4">
      <h2 className="mb-3 text-sm font-semibold text-foreground">
        Create organization
      </h2>
      <form
        className="flex flex-col gap-4"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="so-name">Organization name</Label>
            <Input
              id="so-name"
              value={name}
              maxLength={200}
              onChange={(e) => {
                setName(e.target.value);
                clearFeedback();
              }}
              aria-invalid={fieldErrors.name ? true : undefined}
              placeholder="Acme, Inc."
            />
            <FieldError message={fieldErrors.name} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="so-slug">Slug</Label>
            <Input
              id="so-slug"
              value={slug}
              maxLength={40}
              onChange={(e) => {
                setSlug(e.target.value);
                clearFeedback();
              }}
              aria-invalid={fieldErrors.slug ? true : undefined}
              placeholder="acme"
            />
            <FieldError message={fieldErrors.slug} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="so-owner">Owner email</Label>
            <Input
              id="so-owner"
              type="email"
              value={ownerEmail}
              onChange={(e) => {
                setOwnerEmail(e.target.value);
                clearFeedback();
              }}
              aria-invalid={fieldErrors.ownerEmail ? true : undefined}
              placeholder="owner@acme.com"
            />
            <FieldError message={fieldErrors.ownerEmail} />
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={mut.isPending}>
            Create organization
          </Button>
          {success ? (
            <p className="text-sm text-success" role="status">
              {success}
            </p>
          ) : null}
          {apiError ? (
            <p className="text-sm text-destructive" role="alert">
              {apiError}
            </p>
          ) : null}
        </div>
      </form>
    </section>
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
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-foreground">
                    {m.name || m.email}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {m.email}
                  </div>
                </div>
                <Badge variant="secondary" className="capitalize">
                  {m.role}
                </Badge>
              </li>
            ))}
          </ul>
        )}
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

  const deleteMut = useMutation({
    mutationFn: (org: SuperadminOrgRow) => superadminApi.deleteOrg(org.id),
    onSuccess: (_data, org) => {
      void qc.invalidateQueries({ queryKey: ["superadmin", "orgs"] });
      toast.success(`${org.name} deleted.`);
      setDeletingOrg(null);
    },
    onError: () => {
      toast.error("Couldn't delete that organization.");
      setDeletingOrg(null);
    },
  });

  const reactiveMut = useMutation({
    mutationFn: (args: { org: SuperadminOrgRow; allowed: boolean }) =>
      superadminApi.updateOrgFlags(args.org.id, {
        reactive_cards_allowed: args.allowed,
      }),
    onSuccess: (_data, args) => {
      void qc.invalidateQueries({ queryKey: ["superadmin", "orgs"] });
      toast.success(
        `Reactive cards ${args.allowed ? "enabled" : "disabled"} for ${args.org.name}.`,
      );
    },
    onError: (_err, args) => {
      toast.error(`Couldn't update reactive cards for ${args.org.name}.`);
    },
  });

  const orgs = orgsQ.data ?? [];

  return (
    <section className="overflow-hidden rounded-lg border border-border bg-card">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Organization</TableHead>
            <TableHead>Members</TableHead>
            <TableHead>Pending invites</TableHead>
            <TableHead>Owners</TableHead>
            <TableHead>Reactive cards</TableHead>
            <TableHead>Created</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {orgsQ.isPending ? (
            Array.from({ length: 4 }).map((_, i) => (
              <TableRow key={i}>
                {Array.from({ length: 7 }).map((__, j) => (
                  <TableCell key={j}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                ))}
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
                No organizations.
              </TableCell>
            </TableRow>
          ) : (
            orgs.map((org) => {
              const canDelete = org.member_count <= 1;
              return (
                <TableRow key={org.id}>
                  <TableCell>
                    <div className="font-medium text-foreground">{org.name}</div>
                    <div className="text-xs text-muted-foreground">
                      {org.slug}
                    </div>
                  </TableCell>
                  <TableCell>{org.member_count}</TableCell>
                  <TableCell>{org.pending_invite_count}</TableCell>
                  <TableCell className="max-w-56 truncate text-muted-foreground">
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
                      aria-label={`Reactive cards for ${org.name}`}
                    />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatTimestamp(org.created_at)}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="gap-1.5"
                        onClick={() => setMembersOrg(org)}
                      >
                        <Users />
                        Members
                      </Button>
                      {canDelete ? (
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-1.5 text-destructive hover:text-destructive"
                          onClick={() => setDeletingOrg(org)}
                        >
                          <Trash2 />
                          Delete
                        </Button>
                      ) : (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="inline-flex">
                              <Button
                                variant="outline"
                                size="sm"
                                className="gap-1.5"
                                disabled
                              >
                                <Trash2 />
                                Delete
                              </Button>
                            </span>
                          </TooltipTrigger>
                          <TooltipContent>
                            Remove members before deleting
                          </TooltipContent>
                        </Tooltip>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              );
            })
          )}
        </TableBody>
      </Table>

      <MembersDialog
        org={membersOrg}
        onOpenChange={(o) => {
          if (!o) setMembersOrg(null);
        }}
      />

      <ConfirmDialog
        open={deletingOrg !== null}
        onOpenChange={(o) => {
          if (!o) setDeletingOrg(null);
        }}
        title={deletingOrg ? `Delete ${deletingOrg.name}?` : "Delete organization?"}
        description="This permanently removes the organization. This cannot be undone."
        confirmLabel="Delete organization"
        destructive
        pending={deleteMut.isPending}
        onConfirm={() => {
          if (deletingOrg) deleteMut.mutate(deletingOrg);
        }}
      />
    </section>
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

function ReactiveUsagePanel(): React.ReactElement {
  const [days, setDays] = useState<UsageWindow>(30);
  const usageQ = useQuery({
    queryKey: ["superadmin", "reactiveUsage", days],
    queryFn: () => superadminApi.reactiveUsage({ days }),
  });

  const orgs = usageQ.data?.orgs ?? [];
  const totals = usageQ.data?.totals;

  return (
    <section className="mt-8 overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-3 p-4">
        <div>
          <h2 className="text-sm font-semibold text-foreground">
            Reactive cards usage
          </h2>
          <p className="text-xs text-muted-foreground">
            LLM calls, tokens, and estimated cost per org — monitoring only,
            not a billing surface.
          </p>
        </div>
        <div className="flex gap-1.5">
          {USAGE_WINDOWS.map((w) => (
            <Button
              key={w}
              type="button"
              size="sm"
              variant={days === w ? "default" : "outline"}
              onClick={() => setDays(w)}
            >
              {w}d
            </Button>
          ))}
        </div>
      </div>
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
                Couldn't load reactive-cards usage.
              </TableCell>
            </TableRow>
          ) : orgs.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={7}
                className="py-10 text-center text-sm text-muted-foreground"
              >
                No reactive-cards activity in the last {days} days.
              </TableCell>
            </TableRow>
          ) : (
            <>
              {orgs.map((o) => (
                <TableRow key={o.org_id}>
                  <TableCell className="font-medium text-foreground">
                    {o.org_name}
                  </TableCell>
                  <TableCell className="text-right">{o.generations}</TableCell>
                  <TableCell className="text-right">{o.completed}</TableCell>
                  <TableCell className="text-right">{o.skipped}</TableCell>
                  <TableCell className="text-right">{o.failed}</TableCell>
                  <TableCell className="text-right text-muted-foreground">
                    {formatTokens(o.input_tokens)} / {formatTokens(o.output_tokens)}
                  </TableCell>
                  <TableCell className="text-right">
                    {formatCost(o.cost_usd)}
                  </TableCell>
                </TableRow>
              ))}
              {totals ? (
                <TableRow className="bg-muted/40 font-semibold">
                  <TableCell>All organizations</TableCell>
                  <TableCell className="text-right">{totals.generations}</TableCell>
                  <TableCell className="text-right">{totals.completed}</TableCell>
                  <TableCell className="text-right">{totals.skipped}</TableCell>
                  <TableCell className="text-right">{totals.failed}</TableCell>
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
    </section>
  );
}

export function SuperadminPage(): React.ReactElement {
  return (
    <TooltipProvider>
      <main className="mx-auto w-full max-w-5xl px-4 py-6">
        <div className="mb-6">
          <h1 className="text-xl font-bold text-foreground">Superadmin</h1>
          <p className="text-sm text-muted-foreground">
            Tools across all organizations.
          </p>
        </div>
        <CreateOrgCard />
        <OrgsTableSection />
        <ReactiveUsagePanel />
      </main>
    </TooltipProvider>
  );
}
