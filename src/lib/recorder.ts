// Framework-free MediaRecorder wrapper for client-deck voice answers.
//
// One recorder drives one take. `start()` asks for the mic, `pause()` /
// `resume()` use the native MediaRecorder controls (a paused-then-resumed
// take stays a single continuous file because we buffer every chunk and
// assemble ONE Blob on `stop()`). `stop()` resolves with the recorded
// audio + its mime type and releases the mic track.
//
// `getUserMedia` needs a secure context — that's prod HTTPS and `localhost`
// in dev. Anywhere else (or a browser without MediaRecorder) surfaces a
// clean error the UI shows inline near the record button.

export type RecorderState = "idle" | "recording" | "paused";

export interface RecordingResult {
  blob: Blob;
  mime: string;
}

export class RecorderError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "RecorderError";
  }
}

// Preference order. webm covers Chrome/Firefox/Android; mp4 is the iOS
// Safari fallback. The browser plays back whatever it recorded.
const PREFERRED_MIME_TYPES = ["audio/webm", "audio/mp4"] as const;

function isRecordingSupported(): boolean {
  return (
    typeof MediaRecorder !== "undefined" &&
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia
  );
}

function pickMimeType(): string {
  for (const type of PREFERRED_MIME_TYPES) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  // Let the browser choose its own default container.
  return "";
}

/** File extension for a recording, derived from its mime type, so the
 * uploaded `voice.<ext>` filename is sensible per browser. */
export function extForMime(mime: string): string {
  if (mime.includes("mp4")) return "m4a";
  if (mime.includes("ogg")) return "ogg";
  return "webm";
}

export class Recorder {
  private recorder: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private chunks: Blob[] = [];
  private mime = "";
  private _state: RecorderState = "idle";

  get state(): RecorderState {
    return this._state;
  }

  /** Acquire the mic and begin recording. Throws `RecorderError` with a
   * human-readable message if recording is unsupported or the user denies
   * permission — the caller surfaces it inline. */
  async start(): Promise<void> {
    if (this._state !== "idle") return;
    if (!isRecordingSupported()) {
      throw new RecorderError(
        "Recording is not supported on this browser.",
      );
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      const name = err instanceof DOMException ? err.name : "";
      if (name === "NotAllowedError" || name === "SecurityError") {
        throw new RecorderError(
          "Microphone access was blocked. Allow it in your browser to record.",
        );
      }
      if (name === "NotFoundError") {
        throw new RecorderError("No microphone was found on this device.");
      }
      throw new RecorderError("Could not start recording. Please try again.");
    }

    this.stream = stream;
    this.chunks = [];
    this.mime = pickMimeType();
    const options = this.mime ? { mimeType: this.mime } : undefined;

    try {
      this.recorder = new MediaRecorder(stream, options);
    } catch {
      this.releaseStream();
      throw new RecorderError("Could not start recording. Please try again.");
    }

    this.recorder.ondataavailable = (e: BlobEvent): void => {
      if (e.data && e.data.size > 0) this.chunks.push(e.data);
    };

    this.recorder.start();
    // Capture whatever the browser actually negotiated (it may differ from
    // our request, e.g. an empty preference resolving to a real container).
    this.mime = this.recorder.mimeType || this.mime || "audio/webm";
    this._state = "recording";
  }

  pause(): void {
    if (this._state !== "recording" || !this.recorder) return;
    this.recorder.pause();
    this._state = "paused";
  }

  resume(): void {
    if (this._state !== "paused" || !this.recorder) return;
    this.recorder.resume();
    this._state = "recording";
  }

  /** Stop recording, assemble one continuous Blob from all buffered
   * chunks, and release the mic track. Resolves with the audio + mime. */
  async stop(): Promise<RecordingResult> {
    const recorder = this.recorder;
    if (!recorder || this._state === "idle") {
      return { blob: new Blob([], { type: this.mime }), mime: this.mime };
    }

    try {
      return await new Promise<RecordingResult>((resolve, reject) => {
        recorder.onstop = (): void => {
          const blob = new Blob(this.chunks, { type: this.mime });
          resolve({ blob, mime: this.mime });
        };
        // Some browsers fire `error` instead of (or before) `stop` on a
        // codec/hardware failure. Without this the promise would hang and
        // the mic would never be released. Reject so the caller surfaces it.
        recorder.onerror = (e: Event): void => {
          const err = (e as { error?: DOMException }).error;
          reject(new RecorderError(err?.message ?? "Recording failed."));
        };
        recorder.stop();
      });
    } finally {
      // Always release the mic + reset, even if the recorder errored.
      this.releaseStream();
      this.recorder = null;
      this.chunks = [];
      this._state = "idle";
    }
  }

  /** Stop and discard an in-progress take (e.g. on card navigation) without
   * producing a Blob. Always returns the recorder to `idle`. */
  cancel(): void {
    if (this.recorder && this._state !== "idle") {
      try {
        this.recorder.onstop = null;
        this.recorder.ondataavailable = null;
        this.recorder.stop();
      } catch {
        // Already stopped — nothing to do.
      }
    }
    this.releaseStream();
    this.recorder = null;
    this.chunks = [];
    this._state = "idle";
  }

  private releaseStream(): void {
    if (this.stream) {
      for (const track of this.stream.getTracks()) track.stop();
      this.stream = null;
    }
  }
}
