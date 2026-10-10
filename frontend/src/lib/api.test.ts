import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

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
