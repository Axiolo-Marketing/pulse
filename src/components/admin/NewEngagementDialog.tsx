import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, LoaderCircle, Plus } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { adminApi, ApiError, clientsApi } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

import { Field, ToggleList, ToggleRow } from "./form-parts";
import { RespondentPicker, type PickedRespondent } from "./RespondentPicker";

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
  const [respondents, setRespondents] = useState<PickedRespondent[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Fresh form every time the dialog opens (it stays mounted while closed).
  useEffect(() => {
    if (open) {
      setClientName("");
      setEngagementName("");
      setVoiceEnabled(false);
      setRespondents([]);
      setError(null);
    }
  }, [open]);

  const clientsQ = useQuery({
    queryKey: ["clients"],
    queryFn: () => clientsApi.list(),
  });
  const clients = clientsQ.data ?? [];
  const matchedClient = clients.find(
    (c) => c.name.toLowerCase() === clientName.trim().toLowerCase(),
  );
  // Suggest the chosen client's saved people; a brand-new client has none.
  const contactsQ = useQuery({
    queryKey: ["client-contacts", matchedClient?.id],
    queryFn: () => clientsApi.listContacts(matchedClient!.id),
    enabled: open && !!matchedClient,
  });

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
      // Add respondents one by one — nobody is emailed. A failure on one
      // (e.g. a malformed address the server rejects) doesn't stop the rest.
      const failed: string[] = [];
      for (const r of respondents) {
        try {
          await adminApi.addRecipient(created.id, {
            email: r.email,
            name: r.name ?? undefined,
          });
        } catch {
          failed.push(r.email);
        }
      }
      return { created, failed };
    },
    onSuccess: ({ created, failed }) => {
      void queryClient.invalidateQueries({ queryKey: ["engagements"] });
      void queryClient.invalidateQueries({ queryKey: ["clients"] });
      void queryClient.invalidateQueries({ queryKey: ["client-contacts"] });
      if (failed.length) {
        toast.error(`Couldn't add ${failed.join(", ")}.`, {
          description: "You can add them from the engagement page.",
        });
      }
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

  const query = clientName.trim();
  const lower = query.toLowerCase();
  const matches = clients.filter((c) => c.name.toLowerCase().includes(lower));
  const exact = clients.find((c) => c.name.toLowerCase() === lower);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New engagement</DialogTitle>
          <DialogDescription>
            Pick an existing client or type a new name to create one.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-5"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          noValidate
        >
          <Field id="new-eng-client" label="Client">
            <Command
              shouldFilter={false}
              className="rounded-md border border-input shadow-xs"
            >
              <CommandInput
                id="new-eng-client"
                placeholder="Search clients or type a new name…"
                value={clientName}
                onValueChange={setClientName}
                disabled={submitting}
              />
              <CommandList className="max-h-44">
                {matches.length === 0 && exact === undefined ? (
                  <CommandEmpty>
                    {query
                      ? "No matching clients — create one below."
                      : "Type to search or add a client."}
                  </CommandEmpty>
                ) : null}
                {matches.length > 0 ? (
                  <CommandGroup heading="Existing clients">
                    {matches.map((c) => (
                      <CommandItem
                        key={c.id}
                        value={c.id}
                        onSelect={() => setClientName(c.name)}
                      >
                        {c.name}
                        {exact?.id === c.id ? (
                          <Check className="ml-auto size-4" />
                        ) : null}
                      </CommandItem>
                    ))}
                  </CommandGroup>
                ) : null}
                {query && exact === undefined ? (
                  <CommandGroup heading={matches.length ? "New client" : undefined}>
                    <CommandItem value="__create__" onSelect={() => undefined}>
                      <Plus className="size-4" />
                      Create&nbsp;<span className="font-medium">{query}</span>
                    </CommandItem>
                  </CommandGroup>
                ) : null}
              </CommandList>
            </Command>
          </Field>
          <Field
            id="new-eng-name"
            label="Engagement name"
            optional
            hint="What respondents are weighing in on. You can change it later."
          >
            <Input
              id="new-eng-name"
              value={engagementName}
              onChange={(e) => setEngagementName(e.target.value)}
              disabled={submitting}
              placeholder="e.g. Q3 brand refresh"
            />
          </Field>
          <Field
            id="new-eng-respondents"
            label="Respondents"
            optional
            hint="Nobody is emailed yet. Send invites or copy links from the engagement page."
          >
            <RespondentPicker
              id="new-eng-respondents"
              contacts={contactsQ.data ?? []}
              value={respondents}
              onChange={setRespondents}
              disabled={submitting}
            />
          </Field>
          <ToggleList>
            <ToggleRow
              id="new-eng-voice"
              label="Voice answers"
              description="Respondents can record a voice note on any card."
              checked={voiceEnabled}
              onCheckedChange={setVoiceEnabled}
              disabled={submitting}
            />
          </ToggleList>
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
