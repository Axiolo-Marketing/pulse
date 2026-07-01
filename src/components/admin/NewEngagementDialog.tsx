import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { adminApi, ApiError, clientsApi } from "@/lib/api";
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
import { Switch } from "@/components/ui/switch";

export function NewEngagementDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [clientName, setClientName] = useState("");
  const [engagementName, setEngagementName] = useState("");
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Fresh form every time the dialog opens (it stays mounted while closed).
  useEffect(() => {
    if (open) {
      setClientName("");
      setEngagementName("");
      setVoiceEnabled(false);
      setError(null);
    }
  }, [open]);

  const clientsQ = useQuery({
    queryKey: ["clients"],
    queryFn: () => clientsApi.list(),
  });
  const clients = clientsQ.data ?? [];

  const mutation = useMutation({
    mutationFn: async () => {
      const trimmed = clientName.trim();
      const match = clients.find(
        (c) => c.name.toLowerCase() === trimmed.toLowerCase(),
      );
      const engName = engagementName.trim();
      const created = await adminApi.createEngagement({
        ...(match ? { client_id: match.id } : { client_name: trimmed }),
        engagement_name: engName || null,
      });
      if (voiceEnabled) {
        await adminApi.updateEngagement(created.id, { voice_enabled: true });
      }
      return created;
    },
    onSuccess: (created) => {
      void queryClient.invalidateQueries({ queryKey: ["engagements"] });
      void queryClient.invalidateQueries({ queryKey: ["clients"] });
      onOpenChange(false);
      navigate(`/client/${created.id}`);
    },
    onError: (err) => {
      setError(
        err instanceof ApiError ? err.detail : "Could not create engagement.",
      );
    },
  });

  function submit(): void {
    setError(null);
    if (!clientName.trim()) {
      setError("Client name is required.");
      return;
    }
    mutation.mutate();
  }

  const submitting = mutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New engagement</DialogTitle>
          <DialogDescription>
            Create an engagement for a client. A new client is created if the
            name doesn&apos;t match an existing one.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          noValidate
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="new-eng-client">Client name</Label>
            <Input
              id="new-eng-client"
              list="new-eng-client-options"
              required
              value={clientName}
              onChange={(e) => setClientName(e.target.value)}
              disabled={submitting}
            />
            <datalist id="new-eng-client-options">
              {clients.map((c) => (
                <option key={c.id} value={c.name} />
              ))}
            </datalist>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="new-eng-name">Engagement name</Label>
            <Input
              id="new-eng-name"
              value={engagementName}
              onChange={(e) => setEngagementName(e.target.value)}
              disabled={submitting}
            />
          </div>
          <div className="flex items-center justify-between gap-3">
            <Label htmlFor="new-eng-voice">Enable voice answers</Label>
            <Switch
              id="new-eng-voice"
              checked={voiceEnabled}
              onCheckedChange={setVoiceEnabled}
              disabled={submitting}
            />
          </div>
          {error ? (
            <p className="text-sm font-medium text-destructive" role="alert">
              {error}
            </p>
          ) : null}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitting}>
              {submitting ? (
                <LoaderCircle className="animate-spin" aria-hidden="true" />
              ) : null}
              Create engagement
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
