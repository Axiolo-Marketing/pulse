import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { adminApi, ApiError, type Engagement } from "@/lib/api";
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
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";

export function EditEngagementDialog({
  engagementId,
  engagement,
  open,
  onOpenChange,
}: {
  engagementId: string;
  engagement: Engagement;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const qc = useQueryClient();
  const [name, setName] = useState(engagement.engagement_name ?? "");
  const [voice, setVoice] = useState(engagement.voice_enabled);
  const [reminders, setReminders] = useState(
    engagement.reminders_enabled ?? false,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setName(engagement.engagement_name ?? "");
      setVoice(engagement.voice_enabled);
      setReminders(engagement.reminders_enabled ?? false);
      setError(null);
    }
  }, [open, engagement]);

  const mut = useMutation({
    mutationFn: () =>
      adminApi.updateEngagement(engagementId, {
        engagement_name: name.trim() || null,
        voice_enabled: voice,
        reminders_enabled: reminders,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["engagement", engagementId] });
      void qc.invalidateQueries({ queryKey: ["engagements"] });
      onOpenChange(false);
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.detail : "Could not save."),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit engagement</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ee-name">Engagement name</Label>
            <Input
              id="ee-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Optional"
            />
          </div>
          <div className="flex items-center gap-2">
            <Switch
              id="ee-voice"
              checked={voice}
              onCheckedChange={setVoice}
            />
            <Label htmlFor="ee-voice">Enable voice answers</Label>
          </div>
          <div className="flex items-center gap-2">
            <Switch
              id="ee-reminders"
              checked={reminders}
              onCheckedChange={setReminders}
            />
            <Label htmlFor="ee-reminders">Send reminder emails</Label>
          </div>
          {error ? (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => mut.mutate()} disabled={mut.isPending}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Reusable confirm (AlertDialog) for reset / delete-engagement / delete-card. */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  destructive,
  pending,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  destructive?: boolean;
  pending?: boolean;
  onConfirm: () => void;
}): React.ReactElement {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={onConfirm}
            disabled={pending}
            className={cn(
              destructive &&
                "bg-destructive text-destructive-foreground hover:bg-destructive/90",
            )}
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
