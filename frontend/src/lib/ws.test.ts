import { describe, expect, it } from "vitest";
import { backoffMs, wsUrl } from "./ws";

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
