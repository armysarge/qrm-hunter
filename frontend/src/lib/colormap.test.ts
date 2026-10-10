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
  it("turbo starts near-black and ends bright red/orange (never dark maroon)", () => {
    const lut = buildLut("turbo");
    expect(lut[0] + lut[1] + lut[2]).toBeLessThan(120);
    const [r, g, b] = [lut[1020], lut[1021], lut[1022]];
    expect(r).toBeGreaterThan(200);
    expect(r + g).toBeGreaterThan(250);
    expect(r).toBeGreaterThan(b);
  });
  it("turbo luminance never decreases over the low quarter (noise floor stays dark)", () => {
    const lut = buildLut("turbo");
    const lum = (i: number) => 0.2126 * lut[i * 4] + 0.7152 * lut[i * 4 + 1] + 0.0722 * lut[i * 4 + 2];
    for (let i = 1; i < 64; i++) expect(lum(i)).toBeGreaterThanOrEqual(lum(i - 1));
  });
  it("viridis runs dark purple to yellow", () => {
    const lut = buildLut("viridis");
    expect(lut[0] + lut[1] + lut[2]).toBeLessThan(160);
    expect(lut[1020] + lut[1021]).toBeGreaterThan(450);
  });
});
