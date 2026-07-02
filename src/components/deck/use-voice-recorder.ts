import { useEffect, useRef, useState } from "react";

import { ApiError, clientApi, type UploadRow } from "@/lib/api";
import { extForMime, Recorder, RecorderError } from "@/lib/recorder";

export type VoicePhase =
  | "idle"
  | "recording"
  | "paused"
  | "uploading"
  | "done";

export interface VoiceRecorderApi {
  phase: VoicePhase;
  /** Elapsed recording time in seconds (live while recording). */
  elapsed: number;
  error: string | null;
  /** Object URL for playback of the saved take, or null until fetched. */
  audioUrl: string | null;
  start: () => void;
  pause: () => void;
  resume: () => void;
  stop: () => void;
  remove: () => void;
}

/**
 * Decode a recording and report whether it's effectively silent (no sample
 * crosses a small amplitude floor). Catches the common failure where the mic
 * is OS-blocked or muted: `getUserMedia` still yields a stream, so MediaRecorder
 * produces a well-formed but silent clip. Returns `false` if it can't decode —
 * we don't want an analysis hiccup to block a genuine recording.
 */
async function recordingIsSilent(blob: Blob): Promise<boolean> {
  const Ctor =
    window.AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext })
      .webkitAudioContext;
  if (!Ctor) return false;
  const ctx = new Ctor();
  try {
    const audio = await ctx.decodeAudioData(await blob.arrayBuffer());
    for (let ch = 0; ch < audio.numberOfChannels; ch++) {
      const data = audio.getChannelData(ch);
      for (let i = 0; i < data.length; i++) {
        // 0.002 ≈ -54 dBFS: below any real speech, above a blocked/muted input.
        if (Math.abs(data[i]) >= 0.002) return false;
      }
    }
    return true;
  } catch {
    return false;
  } finally {
    void ctx.close();
  }
}

/**
 * Wraps the framework-free Recorder in a React lifecycle. Mount one per card
 * (key by card id) so unmount cancels any in-progress take and revokes blob
 * URLs. Mirrors the voice state machine in src/scripts/app.ts.
 */
export function useVoiceRecorder({
  token,
  cardId,
  existingUpload,
  onSaved,
  onDeleted,
}: {
  token: string;
  cardId: string;
  existingUpload?: UploadRow;
  onSaved: (row: UploadRow) => void;
  onDeleted: () => void;
}): VoiceRecorderApi {
  const recorderRef = useRef<Recorder | null>(null);
  if (recorderRef.current === null) recorderRef.current = new Recorder();

  const [phase, setPhase] = useState<VoicePhase>(
    existingUpload ? "done" : "idle",
  );
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);

  const accumulatedRef = useRef(0);
  const startedAtRef = useRef(0);
  const mutatingRef = useRef(false);
  const localUrlRef = useRef<string | null>(null);

  const rec = (): Recorder => recorderRef.current as Recorder;

  function revokeLocal(): void {
    if (localUrlRef.current) {
      URL.revokeObjectURL(localUrlRef.current);
      localUrlRef.current = null;
    }
  }

  // Live elapsed-time tick while recording.
  useEffect(() => {
    if (phase !== "recording") return;
    const id = setInterval(() => {
      setElapsed(
        Math.floor(
          (accumulatedRef.current + (Date.now() - startedAtRef.current)) / 1000,
        ),
      );
    }, 250);
    return () => clearInterval(id);
  }, [phase]);

  // Lazily fetch a playback URL for a previously-saved take (returning
  // recipient) — the <audio src> can't carry the auth header.
  useEffect(() => {
    if (!existingUpload || audioUrl) return;
    let active = true;
    void clientApi.fileObjectUrl(token, existingUpload.id).then((url) => {
      if (!active) {
        if (url) URL.revokeObjectURL(url);
        return;
      }
      if (url) {
        localUrlRef.current = url;
        setAudioUrl(url);
      }
    });
    return () => {
      active = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existingUpload?.id]);

  // Cancel any in-progress take + revoke blob URLs when the card changes.
  useEffect(
    () => () => {
      recorderRef.current?.cancel();
      revokeLocal();
    },
    [],
  );

  async function start(): Promise<void> {
    if (mutatingRef.current || rec().state !== "idle") return;
    setError(null);
    mutatingRef.current = true;
    // Re-record: drop the prior take first so there's only ever one.
    if (existingUpload) {
      try {
        await clientApi.deleteUpload(token, existingUpload.id);
      } catch {
        setError("Could not replace the previous recording. Try again.");
        mutatingRef.current = false;
        return;
      }
      revokeLocal();
      setAudioUrl(null);
      onDeleted();
    }
    try {
      await rec().start();
    } catch (err) {
      setError(
        err instanceof RecorderError ? err.message : "Could not start recording.",
      );
      mutatingRef.current = false;
      return;
    }
    accumulatedRef.current = 0;
    startedAtRef.current = Date.now();
    mutatingRef.current = false;
    setElapsed(0);
    setPhase("recording");
  }

  function pause(): void {
    if (rec().state !== "recording") return;
    accumulatedRef.current += Date.now() - startedAtRef.current;
    rec().pause();
    setPhase("paused");
  }

  function resume(): void {
    if (rec().state !== "paused") return;
    rec().resume();
    startedAtRef.current = Date.now();
    setPhase("recording");
  }

  async function stop(): Promise<void> {
    if (rec().state === "idle") return;
    let result: { blob: Blob; mime: string };
    try {
      result = await rec().stop();
    } catch {
      setError("Could not finish the recording. Please try again.");
      setPhase("idle");
      return;
    }
    if (result.blob.size === 0) {
      setError("Nothing was recorded. Please try again.");
      setPhase("idle");
      return;
    }
    if (await recordingIsSilent(result.blob)) {
      setError(
        "We didn't pick up any sound. Check that your microphone is unmuted and allowed for this browser, then record again.",
      );
      setPhase("idle");
      return;
    }
    setError(null);
    setPhase("uploading");
    let row: UploadRow;
    try {
      row = await clientApi.upload(token, cardId, result.blob, {
        kind: "voice",
        filename: `voice.${extForMime(result.mime)}`,
      });
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : "Could not save the recording.",
      );
      setPhase("idle");
      return;
    }
    onSaved(row);
    revokeLocal();
    const url = URL.createObjectURL(result.blob);
    localUrlRef.current = url;
    setAudioUrl(url);
    setPhase("done");
  }

  async function remove(): Promise<void> {
    if (mutatingRef.current || !existingUpload) return;
    mutatingRef.current = true;
    try {
      await clientApi.deleteUpload(token, existingUpload.id);
    } catch {
      setError("Could not delete the recording. Please try again.");
      mutatingRef.current = false;
      return;
    }
    revokeLocal();
    setAudioUrl(null);
    setError(null);
    mutatingRef.current = false;
    onDeleted();
    setPhase("idle");
  }

  return {
    phase,
    elapsed,
    error,
    audioUrl,
    start: () => void start(),
    pause,
    resume,
    stop: () => void stop(),
    remove: () => void remove(),
  };
}
