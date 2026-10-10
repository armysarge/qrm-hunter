import { describe, expect, it } from "vitest";
import { buildLut } from "./colormap";

describe("buildLut", () => {
  for (const name of ["turbo", "viridis", "grey"] as const) {
    it(`${name} has 256 opaque RGBA entries`, () => {
      const lut = buildLut(name);
      expect(lut.length).toBe(1024);
      for (let i = 3; i < 1024; i += 4) expect(lut[i]).toBe(255);
    });
  }
  it("grey runs black to white", () => {
    const lut = buildLut("grey");
    expect(Array.from(lut.slice(0, 3))).toEqual([0, 0, 0]);
    expect(Array.from(lut.slice(1020, 1023))).toEqual([255, 255, 255]);
  });
  it("turbo starts dark and ends red", () => {
    const lut = buildLut("turbo");
    expect(lut[0] + lut[1] + lut[2]).toBeLessThan(200);
    expect(lut[1020]).toBeGreaterThan(lut[1022]);
  });
});
