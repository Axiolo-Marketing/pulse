import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, LoaderCircle, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  ApiError,
  templatesApi,
  type TemplateSummary,
} from "@/lib/api";
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
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

import { Field } from "./form-parts";
import { TemplateQuestionList } from "./TemplateQuestionList";

function formatUpdated(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function TemplateRow({
  template,
  onDelete,
}: {
  template: TemplateSummary;
  onDelete: () => void;
}): React.ReactElement {
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);
  const [name, setName] = useState(template.name);
  const [description, setDescription] = useState(template.description ?? "");
  const [error, setError] = useState<string | null>(null);

  // Follow server data after a save or refetch.
  useEffect(() => {
    setName(template.name);
    setDescription(template.description ?? "");
  }, [template.name, template.description]);

  const detailQ = useQuery({
    queryKey: ["templates", template.id],
    queryFn: () => templatesApi.get(template.id),
    enabled: expanded,
  });

  const dirty =
    name.trim() !== template.name ||
    description.trim() !== (template.description ?? "");

  const saveMut = useMutation({
    mutationFn: () =>
      templatesApi.update(template.id, {
        name: name.trim(),
        description: description.trim() || null,
      }),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["templates"] });
      toast.success("Template saved.");
    },
    onError: (err) =>
      setError(err instanceof ApiError ? err.detail : "Could not save."),
  });

  return (
    <li className="rounded-lg border border-border">
      <button
        type="button"
        className="flex w-full items-start gap-2 px-4 py-3 text-left"
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? (
          <ChevronDown className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        )}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-foreground">
            {template.name}
          </span>
          {template.description ? (
            <span className="block truncate text-xs text-muted-foreground">
              {template.description}
            </span>
          ) : null}
          <span className="block text-xs text-muted-foreground">
            {template.card_count}{" "}
            {template.card_count === 1 ? "question" : "questions"} · Updated{" "}
            {formatUpdated(template.updated_at)}
          </span>
        </span>
      </button>
      {expanded ? (
        <div className="flex flex-col gap-4 border-t border-border px-4 py-4">
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (!name.trim()) {
                setError("Name is required.");
                return;
              }
              saveMut.mutate();
            }}
            noValidate
          >
            <Field id={`tpl-name-${template.id}`} label="Name">
              <Input
                id={`tpl-name-${template.id}`}
                value={name}
                onChange={(e) => setName(e.target.value)}
                disabled={saveMut.isPending}
                maxLength={120}
              />
            </Field>
            <Field id={`tpl-desc-${template.id}`} label="Description" optional>
              <Textarea
                id={`tpl-desc-${template.id}`}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={saveMut.isPending}
                maxLength={500}
                rows={2}
              />
            </Field>
            {error ? (
              <p className="text-sm font-medium text-destructive" role="alert">
                {error}
              </p>
            ) : null}
            <div className="flex items-center justify-between gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={onDelete}
              >
                <Trash2 />
                Delete
              </Button>
              <Button type="submit" size="sm" disabled={!dirty || saveMut.isPending}>
                {saveMut.isPending ? (
                  <LoaderCircle className="animate-spin" aria-hidden="true" />
                ) : null}
                Save changes
              </Button>
            </div>
          </form>
          {detailQ.data ? (
            <TemplateQuestionList cards={detailQ.data.cards} />
          ) : detailQ.isError ? (
            <p className="text-xs text-destructive" role="alert">
              Could not load the questions.
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">Loading questions…</p>
          )}
        </div>
      ) : null}
    </li>
  );
}

export function TemplatesDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}): React.ReactElement {
  const qc = useQueryClient();
  const [deleting, setDeleting] = useState<TemplateSummary | null>(null);

  const listQ = useQuery({
    queryKey: ["templates"],
    queryFn: () => templatesApi.list(),
    enabled: open,
  });
  const templates = listQ.data ?? [];

  const deleteMut = useMutation({
    mutationFn: (id: string) => templatesApi.remove(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["templates"] });
      toast.success("Template deleted.");
      setDeleting(null);
    },
    onError: (err) => {
      toast.error(
        err instanceof ApiError ? err.detail : "Could not delete the template.",
      );
      setDeleting(null);
    },
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Templates</DialogTitle>
          <DialogDescription>
            Reusable decks you can start a new engagement from. Deleting a
            template does not change engagements already created from it.
          </DialogDescription>
        </DialogHeader>
        {listQ.isPending ? (
          <p className="text-sm text-muted-foreground">Loading…</p>
        ) : listQ.isError ? (
          <p className="text-sm text-destructive" role="alert">
            Could not load templates.
          </p>
        ) : templates.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border px-4 py-8 text-center text-sm text-muted-foreground">
            No templates yet. Open an engagement and choose Save as template
            from the menu.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {templates.map((t) => (
              <TemplateRow
                key={t.id}
                template={t}
                onDelete={() => setDeleting(t)}
              />
            ))}
          </ul>
        )}
        {/* Nested inside the dialog on purpose: as a sibling, opening the
            confirm counts as an outside interaction and closes this dialog. */}
        <AlertDialog
          open={!!deleting}
          onOpenChange={(o) => !o && !deleteMut.isPending && setDeleting(null)}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete this template?</AlertDialogTitle>
              <AlertDialogDescription>
                {deleting
                  ? `"${deleting.name}" will be removed. Engagements already started from it keep their questions.`
                  : null}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={deleteMut.isPending}>
                Cancel
              </AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                disabled={deleteMut.isPending}
                onClick={(e) => {
                  e.preventDefault();
                  if (deleting) deleteMut.mutate(deleting.id);
                }}
              >
                Delete
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </DialogContent>
    </Dialog>
  );
}
