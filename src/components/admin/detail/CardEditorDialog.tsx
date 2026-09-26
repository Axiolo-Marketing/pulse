import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlignLeft,
  CircleCheck,
  CircleDot,
  Contact,
  Link2,
  ListChecks,
  LoaderCircle,
  Paperclip,
  TextCursorInput,
  Upload,
  type LucideIcon,
} from "lucide-react";

import {
  adminApi,
  ApiError,
  type Card,
  type CreateCardArgs,
  type ResponseType,
  type UpdateCardArgs,
} from "@/lib/api";
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
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { Field, FormSectionLabel, ToggleList, ToggleRow } from "../form-parts";

const RESPONSE_TYPES: {
  value: ResponseType;
  label: string;
  hint: string;
  icon: LucideIcon;
}[] = [
  { value: "confirm-edit", label: "Confirm or edit", hint: "Confirm a value you pre-fill, or correct it", icon: CircleCheck },
  { value: "single-select", label: "Single choice", hint: "Pick one option", icon: CircleDot },
  { value: "multi-select", label: "Multiple choice", hint: "Pick any number of options", icon: ListChecks },
  { value: "short-text", label: "Short text", hint: "A one-line answer", icon: TextCursorInput },
  { value: "long-text", label: "Long text", hint: "A paragraph or more", icon: AlignLeft },
  { value: "file-upload", label: "File upload", hint: "Attach one or more files", icon: Paperclip },
  { value: "document-link", label: "Link", hint: "Paste a URL to a document", icon: Link2 },
  { value: "contact-share", label: "Contact", hint: "Name, role and email of a person", icon: Contact },
];

/** Response-type picker: a grid of icon tiles (radio group semantics).
 * Locked on edit — the type is immutable once a card exists. */
function ResponseTypePicker({
  value,
  onChange,
  disabled,
}: {
  value: ResponseType;
  onChange: (v: ResponseType) => void;
  disabled?: boolean;
}): React.ReactElement {
  return (
    <div
      role="radiogroup"
      aria-label="Response type"
      className="grid grid-cols-2 gap-2 sm:grid-cols-4"
    >
      {RESPONSE_TYPES.map((rt) => {
        const selected = rt.value === value;
        return (
          <button
            key={rt.value}
            type="button"
            role="radio"
            aria-checked={selected}
            title={rt.hint}
            disabled={disabled && !selected}
            onClick={() => onChange(rt.value)}
            className={cn(
              "flex flex-col items-start gap-2 rounded-lg border p-3 text-left text-sm transition-colors outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
              selected
                ? "border-foreground bg-muted/60 font-medium text-foreground"
                : "border-border text-muted-foreground hover:border-foreground/30 hover:text-foreground",
              disabled && !selected && "cursor-not-allowed opacity-40 hover:border-border hover:text-muted-foreground",
              disabled && selected && "pointer-events-none",
            )}
          >
            <rt.icon className="size-4" aria-hidden="true" />
            <span className="leading-tight">{rt.label}</span>
          </button>
        );
      })}
    </div>
  );
}

function hasOptions(type: ResponseType): boolean {
  return type === "single-select" || type === "multi-select";
}

export function CardEditorDialog({
  engagementId,
  card,
  open,
  onOpenChange,
}: {
  engagementId: string;
  card?: Card;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  const queryClient = useQueryClient();
  const isEditing = card !== undefined;
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [category, setCategory] = useState("");
  const [title, setTitle] = useState("");
  const [context, setContext] = useState("");
  const [question, setQuestion] = useState("");
  const [responseType, setResponseType] = useState<ResponseType>("confirm-edit");
  const [optionsText, setOptionsText] = useState("");
  const [defaultValue, setDefaultValue] = useState("");
  const [skipAllowed, setSkipAllowed] = useState(true);
  const [attachmentPath, setAttachmentPath] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Re-seed the form each time the dialog opens (or the target card changes).
  // The dialog stays mounted while closed, so without this an edit would show
  // stale fields from a prior open.
  useEffect(() => {
    if (!open) return;
    setCategory(card?.category ?? "");
    setTitle(card?.title ?? "");
    setContext(card?.context ?? "");
    setQuestion(card?.question ?? "");
    setResponseType(card?.response_type ?? "confirm-edit");
    setOptionsText((card?.options ?? []).join("\n"));
    setDefaultValue(card?.default_value ?? "");
    setSkipAllowed(card?.skip_allowed ?? true);
    setAttachmentPath(card?.attachment_path ?? "");
    setError(null);
  }, [open, card]);

  const uploadMutation = useMutation({
    mutationFn: (file: File) => adminApi.uploadAttachment(file),
    onSuccess: (res) => {
      setAttachmentPath(res.path);
      setError(null);
    },
    onError: (err) => {
      setError(err instanceof ApiError ? err.detail : "Could not upload file.");
    },
  });

  const mutation = useMutation({
    mutationFn: () => {
      const options = hasOptions(responseType)
        ? optionsText
            .split("\n")
            .map((o) => o.trim())
            .filter((o) => o.length > 0)
        : null;
      const base = {
        category: category.trim(),
        title: title.trim(),
        context: context.trim(),
        question: question.trim(),
        options,
        default_value:
          responseType === "confirm-edit" ? defaultValue.trim() || null : null,
        skip_allowed: skipAllowed,
        attachment_path: attachmentPath.trim() || null,
      };
      if (card) {
        // response_type is immutable on edit.
        const updateArgs: UpdateCardArgs = base;
        return adminApi.updateCard(card.id, updateArgs);
      }
      const createArgs: CreateCardArgs = { ...base, response_type: responseType };
      return adminApi.createCard(engagementId, createArgs);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["engagement", engagementId],
      });
      onOpenChange(false);
    },
    onError: (err) => {
      setError(
        err instanceof ApiError ? err.detail : "Could not save the card.",
      );
    },
  });

  const submitting = mutation.isPending;
  const uploading = uploadMutation.isPending;
  const canSave =
    [category, title, context, question].every((v) => v.trim().length > 0) &&
    !submitting &&
    !uploading;

  function submit(): void {
    setError(null);
    if (!canSave) return;
    mutation.mutate();
  }

  function onFileChosen(e: React.ChangeEvent<HTMLInputElement>): void {
    const file = e.target.files?.[0];
    // Reset so re-selecting the same file fires change again.
    e.target.value = "";
    if (file) uploadMutation.mutate(file);
  }

  const typeHint = RESPONSE_TYPES.find((rt) => rt.value === responseType)?.hint;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isEditing ? "Edit card" : "New card"}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? "Changes show up for respondents the next time they open the deck."
              : "Add a question to the engagement deck."}
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-6"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          noValidate
        >
          <section className="flex flex-col gap-4">
            <FormSectionLabel>Question</FormSectionLabel>
            <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
              <Field id="card-category" label="Category" hint="Groups cards, e.g. Brand.">
                <Input
                  id="card-category"
                  required
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  disabled={submitting}
                />
              </Field>
              <Field id="card-title" label="Title" hint="Short headline shown on the card.">
                <Input
                  id="card-title"
                  required
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  disabled={submitting}
                />
              </Field>
            </div>
            <Field
              id="card-context"
              label="Context"
              hint="Why you're asking, or what you already know."
            >
              <Textarea
                id="card-context"
                required
                rows={3}
                value={context}
                onChange={(e) => setContext(e.target.value)}
                disabled={submitting}
              />
            </Field>
            <Field id="card-question" label="Question">
              <Textarea
                id="card-question"
                required
                rows={2}
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                disabled={submitting}
              />
            </Field>
          </section>

          <section className="flex flex-col gap-4 border-t border-border pt-5">
            <FormSectionLabel>Answer</FormSectionLabel>
            <div className="flex flex-col gap-1.5">
              <ResponseTypePicker
                value={responseType}
                onChange={setResponseType}
                disabled={isEditing || submitting}
              />
              <p className="text-xs text-muted-foreground">
                {isEditing
                  ? "Response type can't be changed after a card is created."
                  : typeHint}
              </p>
            </div>
            {hasOptions(responseType) ? (
              <Field id="card-options" label="Options" hint="One option per line.">
                <Textarea
                  id="card-options"
                  rows={4}
                  value={optionsText}
                  onChange={(e) => setOptionsText(e.target.value)}
                  disabled={submitting}
                />
              </Field>
            ) : null}
            {responseType === "confirm-edit" ? (
              <Field
                id="card-default-value"
                label="Pre-filled value"
                optional
                hint="What the respondent confirms or corrects."
              >
                <Textarea
                  id="card-default-value"
                  rows={2}
                  value={defaultValue}
                  onChange={(e) => setDefaultValue(e.target.value)}
                  disabled={submitting}
                />
              </Field>
            ) : null}
            <Field
              id="card-attachment"
              label="Reference document"
              optional
              hint="Opens beside the question. Upload a file, or enter a deliverables/… path."
            >
              <div className="flex gap-2">
                <Input
                  id="card-attachment"
                  value={attachmentPath}
                  onChange={(e) => setAttachmentPath(e.target.value)}
                  disabled={submitting}
                  placeholder="deliverables/brand-audit.html"
                  className="flex-1 font-mono text-xs"
                />
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  onChange={onFileChosen}
                />
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={submitting || uploading}
                >
                  {uploading ? (
                    <LoaderCircle className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Upload aria-hidden="true" />
                  )}
                  Upload
                </Button>
              </div>
            </Field>
            <ToggleList>
              <ToggleRow
                id="card-skip-allowed"
                label="Allow skipping"
                description="Respondents can move past this card without answering."
                checked={skipAllowed}
                onCheckedChange={setSkipAllowed}
                disabled={submitting}
              />
            </ToggleList>
          </section>

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
            <Button type="submit" disabled={!canSave}>
              {submitting ? (
                <LoaderCircle className="animate-spin" aria-hidden="true" />
              ) : null}
              {isEditing ? "Save changes" : "Create card"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
