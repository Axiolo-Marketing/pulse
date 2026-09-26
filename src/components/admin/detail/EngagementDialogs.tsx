import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { adminApi, ApiError, orgsApi, type Engagement } from "@/lib/api";
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

import { Field, FormSectionLabel, ToggleList, ToggleRow } from "../form-parts";

export function EditEngagementDialog({
  engagementId,
  engagement,
  transcriptionAvailable,
  open,
  onOpenChange,
}: {
  engagementId: string;
  engagement: Engagement;
  /** Whether the server has a transcription provider configured. */
  transcriptionAvailable: boolean;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const qc = useQueryClient();
  const [name, setName] = useState(engagement.engagement_name ?? "");
  const [voice, setVoice] = useState(engagement.voice_enabled);
  const [reminders, setReminders] = useState(
    engagement.reminders_enabled ?? false,
  );
  const [reactiveCards, setReactiveCards] = useState(
    engagement.reactive_cards_enabled ?? false,
  );
  const [transcribe, setTranscribe] = useState(
    engagement.transcription_enabled ?? false,
  );
  const [error, setError] = useState<string | null>(null);

  // Org-level gate for the reactive-cards checkbox — superadmin-managed,
  // not editable here. Reuses the `["orgs", "me"]` cache the Settings page
  // already populates; a cache miss just costs one extra cheap GET.
  const orgQ = useQuery({ queryKey: ["orgs", "me"], queryFn: () => orgsApi.me() });
  const reactiveCardsAllowed = orgQ.data?.reactive_cards_allowed ?? false;

  useEffect(() => {
    if (open) {
      setName(engagement.engagement_name ?? "");
      setVoice(engagement.voice_enabled);
      setReminders(engagement.reminders_enabled ?? false);
      setReactiveCards(engagement.reactive_cards_enabled ?? false);
      setTranscribe(engagement.transcription_enabled ?? false);
      setError(null);
    }
  }, [open, engagement]);

  const mut = useMutation({
    mutationFn: () =>
      adminApi.updateEngagement(engagementId, {
        engagement_name: name.trim() || null,
        voice_enabled: voice,
        reminders_enabled: reminders,
        // Only sent when the org allows it — never resend a stale `true`
        // for an org whose access has since been revoked (the checkbox is
        // disabled in that case; see `reactiveCardsAllowed` below).
        ...(reactiveCardsAllowed
          ? { reactive_cards_enabled: reactiveCards }
          : {}),
        // Only sent when the server can transcribe (turning it on otherwise
        // 400s); an unavailable server leaves the stored flag untouched.
        ...(transcriptionAvailable ? { transcription_enabled: transcribe } : {}),
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
          <DialogDescription>
            Rename this engagement and choose what respondents can do.
          </DialogDescription>
        </DialogHeader>
        <form
          id="edit-engagement-form"
          className="flex flex-col gap-5"
          onSubmit={(e) => {
            e.preventDefault();
            mut.mutate();
          }}
        >
          <Field id="ee-name" label="Engagement name" optional>
            <Input
              id="ee-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Q3 brand refresh"
            />
          </Field>
          <div className="flex flex-col gap-2">
            <FormSectionLabel>Respondent experience</FormSectionLabel>
            <ToggleList>
              <ToggleRow
                id="ee-voice"
                label="Voice answers"
                description="Respondents can record a voice note on any card."
                checked={voice}
                onCheckedChange={setVoice}
              />
              <ToggleRow
                id="ee-reminders"
                label="Reminder emails"
                description="Nudge invited respondents who haven't finished."
                checked={reminders}
                onCheckedChange={setReminders}
              />
              <ToggleRow
                id="ee-transcribe"
                label="Transcribe voice answers"
                description={
                  transcriptionAvailable
                    ? "Voice notes are sent to a speech-to-text service and the text is shown here and in exports. Leave off for clients whose recordings must stay private."
                    : "Not set up on this server."
                }
                checked={transcribe}
                onCheckedChange={setTranscribe}
                disabled={!transcriptionAvailable || !voice}
              />
              <ToggleRow
                id="ee-reactive"
                label="AI follow-up questions"
                description={
                  reactiveCardsAllowed
                    ? "When a respondent corrects an answer, add a short AI-written follow-up card to their deck."
                    : "Not enabled for your organization. Ask an Axiolo admin to turn it on."
                }
                checked={reactiveCards}
                onCheckedChange={setReactiveCards}
                disabled={!reactiveCardsAllowed}
              />
            </ToggleList>
          </div>
          {error ? (
            <p className="text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" form="edit-engagement-form" disabled={mut.isPending}>
            Save changes
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
            variant={destructive ? "destructive" : "default"}
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
