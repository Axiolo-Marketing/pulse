import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy } from "lucide-react";
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
    <section className="rounded-lg border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm font-semibold text-foreground">API keys</h2>
          {keys.length > 0 ? (
            <span className="text-xs text-muted-foreground">{keys.length}</span>
          ) : null}
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          Create key
        </Button>
      </div>

      {keysQuery.isLoading ? (
        <p className="py-2 text-sm text-muted-foreground">Loading…</p>
      ) : keys.length === 0 ? (
        <p className="py-2 text-sm text-muted-foreground">No API keys yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Label</TableHead>
              <TableHead>Key</TableHead>
              <TableHead>Last used</TableHead>
              <TableHead>Created</TableHead>
              <TableHead className="w-0">
                <span className="sr-only">Actions</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {keys.map((k) => (
              <TableRow key={k.id}>
                <TableCell className="font-medium text-foreground">
                  {k.label}
                </TableCell>
                <TableCell className="font-mono text-muted-foreground">
                  pulse_{k.prefix}…
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {k.last_used_at ? formatTimestamp(k.last_used_at) : "Never"}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatTimestamp(k.created_at)}
                </TableCell>
                <TableCell className="text-right">
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
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Revoke
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
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
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.detail : "Could not create key."),
  });

  function copyKey(): void {
    if (!created) return;
    void navigator.clipboard.writeText(created.key);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
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
                <Button variant="outline" onClick={copyKey} className="gap-1.5">
                  <Copy />
                  {copied ? "Copied!" : "Copy"}
                </Button>
              </div>
              <p className="text-sm text-warning">
                You won't see this key again.
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
              <p className="text-sm text-muted-foreground">
                Will be created in {activeOrgName}.
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
