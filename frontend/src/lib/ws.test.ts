import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { backoffMs, ReconnectingSocket, wsUrl } from "./ws";

describe("ws helpers", () => {
  it("backs off exponentially up to 5 s", () => {
    expect([0, 1, 2, 3, 4, 10].map(backoffMs)).toEqual([500, 1000, 2000, 4000, 5000, 5000]);
  });
  it("builds websocket urls with optional token", () => {
    const loc = { protocol: "http:", host: "127.0.0.1:7070" } as Location;
    expect(wsUrl("/ws/events", loc, null)).toBe("ws://127.0.0.1:7070/ws/events");
    const secure = { protocol: "https:", host: "pi.local" } as Location;
    expect(wsUrl("/ws/audio", secure, "a b")).toBe("wss://pi.local/ws/audio?token=a%20b");
  });
});

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  binaryType = "blob";
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((ev: unknown) => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  close() {
    this.closed = true;
    this.readyState = 3;
    queueMicrotask(() => this.onclose?.());
  }
  send() {}
}

describe("ReconnectingSocket", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("location", { protocol: "http:", host: "127.0.0.1:7070", search: "" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports disconnected when stopped while connected", async () => {
    const status: boolean[] = [];
    const s = new ReconnectingSocket("/ws/events", { onMessage: () => {}, onStatus: (ok) => status.push(ok) });
    s.start();
    FakeWebSocket.instances[0].open();
    expect(status).toEqual([true]);
    s.stop();
    expect(status).toEqual([true, false]);
    await Promise.resolve();
    expect(status).toEqual([true, false]); // the late onclose doesn't report twice
    expect(FakeWebSocket.instances[0].closed).toBe(true);
  });

  it("does not report a disconnect when stopped before connecting", () => {
    const status: boolean[] = [];
    const s = new ReconnectingSocket("/ws/events", { onMessage: () => {}, onStatus: (ok) => status.push(ok) });
    s.start();
    s.stop();
    expect(status).toEqual([]);
  });
});
