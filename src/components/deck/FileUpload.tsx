import { useState } from "react";
import { Paperclip, X } from "lucide-react";

import {
  ApiError,
  clientApi,
  type Card as CardModel,
  type UploadRow,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { NoteField } from "./chrome";
import { MAX_FILE_BYTES, MAX_FILES_PER_CARD } from "./constants";

const ACCEPT =
  ".pdf,.docx,.png,.jpg,.jpeg,.csv,.xlsx,application/pdf,image/*,text/csv";

interface Pending {
  tempId: string;
  name: string;
  error?: string;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function FileUploadInput({
  card,
  saving,
  token,
  existingFiles,
  hasVoice,
  onUploaded,
  onRemoved,
  onContinue,
  onSkip,
}: {
  card: CardModel;
  saving: boolean;
  token: string;
  existingFiles: UploadRow[];
  hasVoice: boolean;
  onUploaded: (row: UploadRow) => void;
  onRemoved: (uploadId: string) => void;
  onContinue: (note?: string) => void;
  onSkip: (note?: string) => void;
}): React.ReactElement {
  const [pending, setPending] = useState<Pending[]>([]);
  const [removing, setRemoving] = useState<Set<string>>(new Set());
  const [note, setNote] = useState("");

  async function removeFile(uploadId: string): Promise<void> {
    setRemoving((s) => new Set(s).add(uploadId));
    try {
      await clientApi.deleteUpload(token, uploadId);
      onRemoved(uploadId);
    } catch {
      // Leave the file in place so the recipient can retry the removal.
    } finally {
      setRemoving((s) => {
        const n = new Set(s);
        n.delete(uploadId);
        return n;
      });
    }
  }

  const activePending = pending.filter((p) => !p.error).length;
  const remaining = MAX_FILES_PER_CARD - existingFiles.length - activePending;
  const full = remaining <= 0;
  const hasFiles = existingFiles.length > 0;
  const continueDisabled = saving || activePending > 0 || (!hasFiles && !hasVoice);

  async function handleFiles(files: FileList): Promise<void> {
    const room = MAX_FILES_PER_CARD - existingFiles.length - activePending;
    for (const file of Array.from(files).slice(0, Math.max(0, room))) {
      const tempId = crypto.randomUUID();
      if (file.size > MAX_FILE_BYTES) {
        setPending((p) => [
          ...p,
          { tempId, name: file.name, error: "Too large (max 25MB)" },
        ]);
        continue;
      }
      setPending((p) => [...p, { tempId, name: file.name }]);
      try {
        const row = await clientApi.upload(token, card.id, file);
        onUploaded(row);
        setPending((p) => p.filter((x) => x.tempId !== tempId));
      } catch (err) {
        const detail = err instanceof ApiError ? err.detail : "Upload failed";
        setPending((p) =>
          p.map((x) => (x.tempId === tempId ? { ...x, error: detail } : x)),
        );
      }
    }
  }

  return (
    <>
      {full ? (
        <div className="rounded-lg border border-border bg-muted px-4 py-4 text-center text-sm text-muted-foreground">
          Maximum of {MAX_FILES_PER_CARD} files reached. Remove one to add
          another.
        </div>
      ) : (
        <label
          className={cn(
            "flex min-h-28 cursor-pointer flex-col items-center justify-center gap-1.5 rounded-lg border-2 border-dashed border-primary/40 bg-secondary px-4 py-6 text-center",
            saving && "pointer-events-none opacity-60",
          )}
        >
          <Paperclip className="size-5 text-primary" aria-hidden="true" />
          <span className="text-sm font-medium text-foreground">
            Tap to upload or drop files here
          </span>
          <span className="text-xs text-muted-foreground">
            Up to {remaining} more file{remaining === 1 ? "" : "s"}, max 25MB
            each
          </span>
          <input
            type="file"
            multiple
            accept={ACCEPT}
            disabled={saving}
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) void handleFiles(e.target.files);
              e.target.value = "";
            }}
          />
        </label>
      )}

      {existingFiles.length > 0 || pending.length > 0 ? (
        <div className="mt-3 flex flex-col gap-2">
          {existingFiles.map((u) => (
            <div
              key={u.id}
              className="flex items-center gap-3 rounded-lg border-[1.5px] border-border bg-card px-3 py-2.5 text-sm"
            >
              <span className="flex-1 truncate">{u.file_name}</span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {formatSize(u.file_size_bytes)}
              </span>
              <button
                type="button"
                onClick={() => void removeFile(u.id)}
                disabled={saving || removing.has(u.id)}
                aria-label={`Remove ${u.file_name}`}
                className="shrink-0 text-muted-foreground hover:text-destructive disabled:opacity-50 [&_svg]:size-4"
              >
                <X aria-hidden="true" />
              </button>
            </div>
          ))}
          {pending.map((p) => (
            <div
              key={p.tempId}
              className={cn(
                "flex items-center gap-3 rounded-lg border-[1.5px] px-3 py-2.5 text-sm",
                p.error
                  ? "border-destructive/40 bg-card"
                  : "border-primary/40 bg-secondary",
              )}
            >
              <span className="flex-1 truncate">{p.name}</span>
              <span className="shrink-0 text-xs">
                {p.error ? (
                  <span className="text-destructive">{p.error}</span>
                ) : (
                  <span className="text-muted-foreground">Uploading…</span>
                )}
              </span>
            </div>
          ))}
        </div>
      ) : null}

      <NoteField value={note} onChange={setNote} disabled={saving} />
      <div className="mt-6 flex flex-col gap-2.5">
        <Button
          type="button"
          disabled={continueDisabled}
          onClick={() => onContinue(note.trim() || undefined)}
        >
          {saving ? "Saving…" : "Continue"}
        </Button>
        {card.skip_allowed ? (
          <Button
            variant="ghost"
            type="button"
            disabled={saving}
            onClick={() => onSkip(note.trim() || undefined)}
            className="text-muted-foreground"
          >
            Skip for now
          </Button>
        ) : null}
      </div>
    </>
  );
}
