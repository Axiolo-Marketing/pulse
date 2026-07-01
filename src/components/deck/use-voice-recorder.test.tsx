import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, clientApi, type UploadRow } from "@/lib/api";
import { useVoiceRecorder } from "./use-voice-recorder";

// A controllable fake in place of the real MediaRecorder-backed Recorder.
vi.mock("@/lib/recorder", () => {
  class FakeRecorder {
    state: "idle" | "recording" | "paused" = "idle";
    start = vi.fn(async () => {
      this.state = "recording";
    });
    pause = vi.fn(() => {
      this.state = "paused";
    });
    resume = vi.fn(() => {
      this.state = "recording";
    });
    stop = vi.fn(async () => {
      this.state = "idle";
      return { blob: new Blob(["audio"], { type: "audio/webm" }), mime: "audio/webm" };
    });
    cancel = vi.fn(() => {
      this.state = "idle";
    });
  }
  return {
    Recorder: FakeRecorder,
    RecorderError: class RecorderError extends Error {},
    extForMime: () => "webm",
  };
});

vi.mock("@/lib/api", async (importActual) => {
  const actual = await importActual<typeof import("@/lib/api")>();
  return {
    ...actual,
    clientApi: {
      ...actual.clientApi,
      upload: vi.fn(),
      deleteUpload: vi.fn().mockResolvedValue(undefined),
      fileObjectUrl: vi.fn().mockResolvedValue(null),
    },
  };
});

const uploadRow = {
  id: "v1",
  card_id: "c1",
  file_name: "voice.webm",
  file_size_bytes: 100,
  kind: "voice",
} as UploadRow;

function setup(existingUpload?: UploadRow) {
  const onSaved = vi.fn();
  const onDeleted = vi.fn();
  const view = renderHook(() =>
    useVoiceRecorder({
      token: "t",
      cardId: "c1",
      existingUpload,
      onSaved,
      onDeleted,
    }),
  );
  return { ...view, onSaved, onDeleted };
}

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:fake");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("useVoiceRecorder", () => {
  it("starts idle with no prior take and done with one", () => {
    expect(setup().result.current.phase).toBe("idle");
    expect(setup(uploadRow).result.current.phase).toBe("done");
  });

  it("records, pauses, resumes", async () => {
    const { result } = setup();
    await act(async () => {
      result.current.start();
    });
    expect(result.current.phase).toBe("recording");
    act(() => result.current.pause());
    expect(result.current.phase).toBe("paused");
    act(() => result.current.resume());
    expect(result.current.phase).toBe("recording");
  });

  it("stops → uploads → done, calling onSaved with the row", async () => {
    vi.mocked(clientApi.upload).mockResolvedValue(uploadRow);
    const { result, onSaved } = setup();
    await act(async () => {
      result.current.start();
    });
    await act(async () => {
      result.current.stop();
    });
    await waitFor(() => expect(result.current.phase).toBe("done"));
    expect(clientApi.upload).toHaveBeenCalledWith(
      "t",
      "c1",
      expect.any(Blob),
      { kind: "voice", filename: "voice.webm" },
    );
    expect(onSaved).toHaveBeenCalledWith(uploadRow);
  });

  it("surfaces an error and returns to idle when the upload fails", async () => {
    vi.mocked(clientApi.upload).mockRejectedValue(new ApiError(500, "boom"));
    const { result } = setup();
    await act(async () => {
      result.current.start();
    });
    await act(async () => {
      result.current.stop();
    });
    await waitFor(() => expect(result.current.phase).toBe("idle"));
    expect(result.current.error).toBe("boom");
  });

  it("deletes an existing take and notifies the parent", async () => {
    const { result, onDeleted } = setup(uploadRow);
    expect(result.current.phase).toBe("done");
    await act(async () => {
      result.current.remove();
    });
    await waitFor(() => expect(result.current.phase).toBe("idle"));
    expect(clientApi.deleteUpload).toHaveBeenCalledWith("t", "v1");
    expect(onDeleted).toHaveBeenCalled();
  });
});
