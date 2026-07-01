import { LoaderCircle, Mic, Pause, Play, Square, Trash2 } from "lucide-react";

import type { UploadRow } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { useVoiceRecorder } from "./use-voice-recorder";

function fmt(sec: number): string {
  const m = Math.floor(sec / 60)
    .toString()
    .padStart(2, "0");
  const s = (sec % 60).toString().padStart(2, "0");
  return `${m}:${s}`;
}

export function VoiceRecorder({
  token,
  cardId,
  existingUpload,
  disabled,
  onSaved,
  onDeleted,
}: {
  token: string;
  cardId: string;
  existingUpload?: UploadRow;
  disabled?: boolean;
  onSaved: (row: UploadRow) => void;
  onDeleted: () => void;
}): React.ReactElement {
  const v = useVoiceRecorder({
    token,
    cardId,
    existingUpload,
    onSaved,
    onDeleted,
  });

  return (
    <div>
      <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Prefer to talk?
      </p>

      {v.phase === "idle" ? (
        <Button
          variant="outline"
          type="button"
          disabled={disabled}
          onClick={v.start}
          className="w-full"
        >
          <Mic />
          Record answer
        </Button>
      ) : null}

      {v.phase === "recording" || v.phase === "paused" ? (
        <div className="flex flex-col gap-3">
          <div
            className={cn(
              "flex items-center gap-2.5 rounded-lg px-4 py-2.5",
              v.phase === "recording" ? "bg-warning-soft" : "bg-muted",
            )}
          >
            <span
              className={cn(
                "size-3 rounded-full",
                v.phase === "recording"
                  ? "animate-pulse bg-warning"
                  : "bg-muted-foreground",
              )}
            />
            <span className="text-sm font-medium text-foreground">
              {v.phase === "recording" ? "Recording" : "Paused"}
            </span>
            <span className="ml-auto font-semibold tabular-nums text-foreground">
              {fmt(v.elapsed)}
            </span>
          </div>
          <div className="flex gap-2">
            {v.phase === "recording" ? (
              <Button
                variant="outline"
                type="button"
                onClick={v.pause}
                className="flex-1"
              >
                <Pause />
                Pause
              </Button>
            ) : (
              <Button
                variant="outline"
                type="button"
                onClick={v.resume}
                className="flex-1"
              >
                <Play />
                Resume
              </Button>
            )}
            <Button type="button" onClick={v.stop} className="flex-1">
              <Square />
              Stop
            </Button>
          </div>
        </div>
      ) : null}

      {v.phase === "uploading" ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle className="animate-spin" aria-hidden="true" />
          Saving recording…
        </div>
      ) : null}

      {v.phase === "done" ? (
        <div className="flex flex-col gap-3">
          {v.audioUrl ? (
            <audio
              controls
              preload="metadata"
              src={v.audioUrl}
              className="w-full"
            />
          ) : (
            <p className="text-sm text-muted-foreground">Recording saved.</p>
          )}
          <div className="flex gap-2">
            <Button
              variant="outline"
              type="button"
              disabled={disabled}
              onClick={v.start}
              className="flex-1"
            >
              <Mic />
              Re-record
            </Button>
            <Button
              variant="ghost"
              type="button"
              disabled={disabled}
              onClick={v.remove}
              className="text-destructive"
            >
              <Trash2 />
              Delete
            </Button>
          </div>
        </div>
      ) : null}

      {v.error ? (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {v.error}
        </p>
      ) : null}
    </div>
  );
}
