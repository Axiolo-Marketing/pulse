import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import { toast } from "sonner";

import { ApiError, templatesApi } from "@/lib/api";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import { Field } from "../form-parts";

type Mode = "new" | "replace";

export function SaveTemplateDialog({
  engagementId,
  questionCount,
  open,
  onOpenChange,
}: {
  engagementId: string;
  /** How many questions will be saved (the shared operator-written ones). */
  questionCount: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>("new");
  const [targetId, setTargetId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  const listQ = useQuery({
    queryKey: ["templates"],
    queryFn: () => templatesApi.list(),
    enabled: open,
  });
  const templates = listQ.data ?? [];
  const target = templates.find((t) => t.id === targetId);

  useEffect(() => {
    if (open) {
      setMode("new");
      setTargetId("");
      setName("");
      setDescription("");
      setError(null);
    }
  }, [open]);

  function chooseMode(next: Mode): void {
    setMode(next);
    setError(null);
    if (next === "new") {
      setTargetId("");
      setName("");
      setDescription("");
    }
  }

  function chooseTarget(id: string): void {
    setTargetId(id);
    setError(null);
    const t = templates.find((x) => x.id === id);
    setName(t?.name ?? "");
    setDescription(t?.description ?? "");
  }

  const mut = useMutation({
    mutationFn: () =>
      mode === "replace"
        ? templatesApi.update(targetId, {
            engagement_id: engagementId,
            name: name.trim(),
            description: description.trim() || null,
          })
        : templatesApi.create({
            engagement_id: engagementId,
            name: name.trim(),
            description: description.trim() || null,
          }),
    onSuccess: (t) => {
      void qc.invalidateQueries({ queryKey: ["templates"] });
      toast.success(
        mode === "replace"
          ? `Template "${t.name}" updated.`
          : `Saved as template "${t.name}".`,
      );
      onOpenChange(false);
    },
    onError: (err) =>
      setError(
        err instanceof ApiError ? err.detail : "Could not save the template.",
      ),
  });

  function submit(): void {
    setError(null);
    if (mode === "replace" && !targetId) {
      setError("Choose a template to replace.");
      return;
    }
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    mut.mutate();
  }

  const pending = mut.isPending;
  const canReplace = templates.length > 0;

  return (
    <Dialog open={open} onOpenChange={(o) => !pending && onOpenChange(o)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Save as template</DialogTitle>
          <DialogDescription>
            Copy this deck's {questionCount}{" "}
            {questionCount === 1 ? "question" : "questions"} and settings so
            you can start future engagements from it. Answers and respondents
            are not saved.
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
          {canReplace ? (
            <div
              role="radiogroup"
              aria-label="Save mode"
              className="grid grid-cols-2 gap-2"
            >
              {(
                [
                  ["new", "New template"],
                  ["replace", "Replace an existing template"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={mode === value}
                  disabled={pending}
                  onClick={() => chooseMode(value)}
                  className={
                    "rounded-lg border px-3 py-2 text-left text-sm font-medium " +
                    (mode === value
                      ? "border-foreground bg-accent"
                      : "border-border hover:bg-accent/50")
                  }
                >
                  {label}
                </button>
              ))}
            </div>
          ) : null}
          {mode === "replace" ? (
            <Field id="save-tpl-target" label="Template to replace">
              <Select
                value={targetId}
                onValueChange={chooseTarget}
                disabled={pending}
              >
                <SelectTrigger id="save-tpl-target" className="w-full">
                  <SelectValue placeholder="Choose a template" />
                </SelectTrigger>
                <SelectContent>
                  {templates.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.name} ({t.card_count}{" "}
                      {t.card_count === 1 ? "question" : "questions"})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {target ? (
                <p className="text-xs text-foreground">
                  This overwrites the {target.card_count}{" "}
                  {target.card_count === 1 ? "question" : "questions"} and
                  settings in "{target.name}" with this engagement's. Past
                  engagements started from it are not affected.
                </p>
              ) : null}
            </Field>
          ) : null}
          <Field id="save-tpl-name" label="Name">
            <Input
              id="save-tpl-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={pending}
              maxLength={120}
              placeholder="e.g. Brand discovery"
            />
          </Field>
          <Field id="save-tpl-desc" label="Description" optional>
            <Textarea
              id="save-tpl-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={pending}
              maxLength={500}
              rows={2}
            />
          </Field>
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
              disabled={pending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={pending}>
              {pending ? (
                <LoaderCircle className="animate-spin" aria-hidden="true" />
              ) : null}
              {mode === "replace" ? "Replace template" : "Save template"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
