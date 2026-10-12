import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, withToken } from "./api";

function respond(status: number, statusText: string, body: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(body, { status, statusText })),
  );
}

async function errorOf(p: Promise<unknown>): Promise<ApiError> {
  try {
    await p;
  } catch (e) {
    return e as ApiError;
  }
  throw new Error("expected a rejection");
}

describe("api errors", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses a string detail", async () => {
    respond(409, "Conflict", JSON.stringify({ detail: "device busy" }));
    const e = await errorOf(api("/api/x"));
    expect(e).toBeInstanceOf(ApiError);
    expect(e.status).toBe(409);
    expect(e.message).toBe("device busy");
  });

  it("stringifies a structured detail", async () => {
    respond(422, "Unprocessable Entity", JSON.stringify({ detail: [{ msg: "bad" }] }));
    expect((await errorOf(api("/api/x"))).message).toBe('[{"msg":"bad"}]');
  });

  it("falls back to HTTP status when the body has no detail", async () => {
    respond(500, "Internal Server Error", JSON.stringify({ oops: 1 }));
    expect((await errorOf(api("/api/x"))).message).toBe("HTTP 500 Internal Server Error");
  });

  it("falls back to HTTP status for a non-JSON body without statusText", async () => {
    respond(502, "", "<html>bad gateway</html>");
    expect((await errorOf(api("/api/x"))).message).toBe("HTTP 502");
  });

  it("falls back when detail is an empty string", async () => {
    respond(400, "", JSON.stringify({ detail: "" }));
    expect((await errorOf(api("/api/x"))).message).toBe("HTTP 400");
  });
});

describe("api errors (null detail)", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("treats detail: null as missing", async () => {
    respond(503, "Service Unavailable", JSON.stringify({ detail: null }));
    expect((await errorOf(api("/api/x"))).message).toBe("HTTP 503 Service Unavailable");
  });
});

describe("state-changing requests", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("send Content-Type: application/json, including close with no parameters", async () => {
    const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { closeSession, openSession, patchState, patchSettings } = await import("./api");
    await closeSession();
    await openSession("synthetic", { tune_hz: 7_100_000 });
    await patchState({ mode: "USB" });
    await patchSettings({ theme: "light" });
    expect(fetchMock).toHaveBeenCalledTimes(4);
    for (const [, init] of fetchMock.mock.calls) {
      expect(["POST", "PATCH"]).toContain(init?.method);
      expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
      expect(typeof init?.body).toBe("string");
    }
  });
});

describe("memory client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends JSON bodies for every memory write, including DELETE, and handles 204", async () => {
    const fetchMock = vi.fn(async (_path: string, init?: RequestInit) =>
      init?.method === "DELETE" ? new Response(null, { status: 204 }) : new Response('{"added":1,"skipped":0}', { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { createMemory, deleteMemory, importMemories, updateMemory } = await import("./api");
    const fields = { name: "x", tune_hz: 1e6, mode: "AM" as const, bw_hz: 6000, gain_db: null, direct_sampling: null, note: "" };
    await createMemory(fields);
    await updateMemory("a/b", { note: "n" });
    expect(await deleteMemory("id1")).toBeUndefined();
    expect(await importMemories("csv", "data")).toEqual({ added: 1, skipped: 0 });
    const calls = fetchMock.mock.calls.map(([p, i]) => [p, i?.method]);
    expect(calls).toEqual([["/api/memories", "POST"], ["/api/memories/a%2Fb", "PATCH"],
      ["/api/memories/id1", "DELETE"], ["/api/memories/import", "POST"]]);
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    }
    expect(JSON.parse(fetchMock.mock.calls[3][1]!.body as string)).toEqual({ format: "csv", data: "data", replace: false });
  });

  it("fetches exports as a blob and surfaces errors", async () => {
    const fetchMock = vi.fn(async (path: string) =>
      path.endsWith("csv") ? new Response("name,tune_hz\n", { status: 200 }) : new Response('{"detail":"bad"}', { status: 422 }));
    vi.stubGlobal("fetch", fetchMock);
    const { exportMemories, listMemories } = await import("./api");
    expect(await (await exportMemories("csv")).text()).toBe("name,tune_hz\n");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/memories/export?format=csv");
    expect((await errorOf(listMemories())).message).toBe("bad");
  });
});

describe("withToken", () => {
  it("adds the token for links that cannot send headers", () => {
    expect(withToken("/api/recordings/a/waterfall.png", "?token=t k")).toBe("/api/recordings/a/waterfall.png?token=t%20k");
    expect(withToken("/api/x", "")).toBe("/api/x");
  });
});
