import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, Upload } from "lucide-react";

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
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";

const RESPONSE_TYPES: { value: ResponseType; label: string }[] = [
  { value: "confirm-edit", label: "Confirm / edit" },
  { value: "single-select", label: "Single select" },
  { value: "multi-select", label: "Multi select" },
  { value: "short-text", label: "Short text" },
  { value: "long-text", label: "Long text" },
  { value: "file-upload", label: "File upload" },
  { value: "document-link", label: "Document link" },
  { value: "contact-share", label: "Contact share" },
];

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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEditing ? "Edit card" : "New card"}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? "Update this card in the engagement deck."
              : "Add a card to the engagement deck."}
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
            <Label htmlFor="card-category">Category</Label>
            <Input
              id="card-category"
              required
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              disabled={submitting}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="card-title">Title</Label>
            <Input
              id="card-title"
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              disabled={submitting}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="card-context">Context</Label>
            <Textarea
              id="card-context"
              required
              value={context}
              onChange={(e) => setContext(e.target.value)}
              disabled={submitting}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="card-question">Question</Label>
            <Textarea
              id="card-question"
              required
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              disabled={submitting}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="card-response-type">Response type</Label>
            <Select
              value={responseType}
              onValueChange={(v) => setResponseType(v as ResponseType)}
              disabled={isEditing || submitting}
            >
              <SelectTrigger id="card-response-type" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {RESPONSE_TYPES.map((rt) => (
                  <SelectItem key={rt.value} value={rt.value}>
                    {rt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {isEditing ? (
              <p className="text-xs text-muted-foreground">
                Response type can&apos;t be changed after a card is created.
              </p>
            ) : null}
          </div>
          {hasOptions(responseType) ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="card-options">Options</Label>
              <Textarea
                id="card-options"
                value={optionsText}
                onChange={(e) => setOptionsText(e.target.value)}
                disabled={submitting}
                placeholder="One option per line"
              />
            </div>
          ) : null}
          {responseType === "confirm-edit" ? (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="card-default-value">Default value</Label>
              <Textarea
                id="card-default-value"
                value={defaultValue}
                onChange={(e) => setDefaultValue(e.target.value)}
                disabled={submitting}
                placeholder="The value shown for confirmation"
              />
            </div>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="card-attachment">Attachment path</Label>
            <div className="flex gap-2">
              <Input
                id="card-attachment"
                value={attachmentPath}
                onChange={(e) => setAttachmentPath(e.target.value)}
                disabled={submitting}
                placeholder="Optional"
                className="flex-1"
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
                Upload file
              </Button>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Switch
              id="card-skip-allowed"
              checked={skipAllowed}
              onCheckedChange={setSkipAllowed}
              disabled={submitting}
            />
            <Label htmlFor="card-skip-allowed">Allow skipping</Label>
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
