import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Plus, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import {
  authApi,
  ApiError,
  type ApiKeySummary,
  type ApiKeyWithSecret,
} from "@/lib/api";
import { formatTimestamp } from "@/lib/format-time";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import { SettingsSection } from "./parts";

export function ApiKeysManager({
  activeOrgName,
}: {
  activeOrgName: string;
}): React.ReactElement {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [revoking, setRevoking] = useState<ApiKeySummary | null>(null);

  const keysQuery = useQuery({
    queryKey: ["apiKeys"],
    queryFn: () => authApi.listApiKeys(),
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => authApi.revokeApiKey(id),
    onSuccess: () => {
      setRevoking(null);
      void qc.invalidateQueries({ queryKey: ["apiKeys"] });
      toast.success("API key revoked.");
    },
    onError: (err) => {
      setRevoking(null);
      toast.error(
        err instanceof ApiError ? err.detail : "Couldn't revoke that key.",
      );
    },
  });

  const keys = keysQuery.data ?? [];

  return (
    <SettingsSection
      title="API keys"
      description={
        <>
          Let scripts and the Pulse MCP server act as you in{" "}
          <span className="font-medium text-foreground">{activeOrgName}</span>.
        </>
      }
      action={
        <Button size="sm" variant="outline" onClick={() => setCreateOpen(true)}>
          <Plus />
          Create key
        </Button>
      }
      flush
    >
      {keysQuery.isLoading ? (
        <p className="px-5 py-4 text-sm text-muted-foreground">Loading…</p>
      ) : keys.length === 0 ? (
        <div className="flex flex-col items-center gap-1 px-5 py-8 text-center">
          <KeyRound className="mb-1 size-5 text-muted-foreground" />
          <p className="text-sm font-medium text-foreground">No API keys yet</p>
          <p className="text-sm text-muted-foreground">
            Create one to connect a script or Claude via MCP.
          </p>
        </div>
      ) : (
        <Table>
          <TableHeader className="bg-muted/40">
            <TableRow>
              <TableHead className="pl-5">Label</TableHead>
              <TableHead>Key</TableHead>
              <TableHead className="hidden sm:table-cell">Last used</TableHead>
              <TableHead className="hidden sm:table-cell">Created</TableHead>
              <TableHead className="w-0 pr-5">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.map((k) => (
              <TableRow key={k.id}>
                <TableCell className="pl-5 font-medium text-foreground">
                  {k.label}
                </TableCell>
                <TableCell className="font-mono text-xs text-muted-foreground">
                  pulse_{k.prefix}…
                </TableCell>
                <TableCell className="hidden text-muted-foreground sm:table-cell">
                  {k.last_used_at ? formatTimestamp(k.last_used_at) : "Never"}
                </TableCell>
                <TableCell className="hidden text-muted-foreground sm:table-cell">
                  {formatTimestamp(k.created_at)}
                </TableCell>
                <TableCell className="pr-5 text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setRevoking(k)}
                    className="text-muted-foreground hover:text-destructive"
                  >
                    Revoke
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}

      <CreateKeyDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        activeOrgName={activeOrgName}
      />

      <AlertDialog
        open={revoking !== null}
        onOpenChange={(o) => {
          if (!o) setRevoking(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revoke this key?</AlertDialogTitle>
            <AlertDialogDescription>
              Anything using it stops working immediately.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (revoking) revokeMut.mutate(revoking.id);
              }}
              disabled={revokeMut.isPending}
              variant="destructive"
            >
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SettingsSection>
  );
}

function CreateKeyDialog({
  open,
  onOpenChange,
  activeOrgName,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  activeOrgName: string;
}): React.ReactElement {
  const qc = useQueryClient();
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<ApiKeyWithSecret | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (open) {
      setLabel("");
      setError(null);
      setCreated(null);
      setCopied(false);
    }
  }, [open]);

  const createMut = useMutation({
    mutationFn: () => authApi.createApiKey({ label: label.trim() }),
    onSuccess: (key) => {
      setCreated(key);
      setError(null);
      // The dialog can be dismissed (Esc / X / outside click) without
      // hitting Done, so refresh the list as soon as the key exists.
      void qc.invalidateQueries({ queryKey: ["apiKeys"] });
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.detail : "Could not create key."),
  });

  async function copyKey(): Promise<void> {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.key);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      toast.error("Could not copy — select the field and copy manually.");
    }
  }

  function done(): void {
    onOpenChange(false);
    void qc.invalidateQueries({ queryKey: ["apiKeys"] });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {created ? (
          <>
            <DialogHeader>
              <DialogTitle>API key created</DialogTitle>
              <DialogDescription>
                Copy it and store it somewhere safe.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ak-secret">Your API key</Label>
              <div className="flex gap-2">
                <Input
                  id="ak-secret"
                  readOnly
                  value={created.key}
                  className="font-mono"
                  onFocus={(e) => e.target.select()}
                />
                <Button variant="outline" onClick={() => void copyKey()} className="gap-1.5">
                  <Copy />
                  {copied ? "Copied!" : "Copy"}
                </Button>
              </div>
              <p className="mt-2 flex items-start gap-2 rounded-md border border-warning/40 bg-warning-soft px-3 py-2 text-sm text-amber-800">
                <TriangleAlert
                  className="mt-0.5 size-4 shrink-0"
                  aria-hidden="true"
                />
                You won't be able to see this key again. Copy it now.
              </p>
            </div>
            <DialogFooter>
              <Button onClick={done}>Done</Button>
            </DialogFooter>
          </>
        ) : (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              createMut.mutate();
            }}
          >
            <DialogHeader>
              <DialogTitle>Create API key</DialogTitle>
              <DialogDescription>
                The key acts as you in {activeOrgName}.
              </DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ak-label">Label</Label>
              <Input
                id="ak-label"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                maxLength={100}
                required
                autoFocus
                placeholder="e.g. CI deploy bot"
              />
              <p className="text-xs text-muted-foreground">
                A name to recognise it by later.
              </p>
            </div>
            {error ? (
              <p className="text-sm text-destructive" role="alert">
                {error}
              </p>
            ) : null}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={createMut.isPending || !label.trim()}
              >
                Create key
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
