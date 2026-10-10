import { describe, expect, it } from "vitest";
import { HEADER_BYTES, parseSpectrumFrame } from "./protocol";

function frame(bins: number[], opts: { magic?: string; version?: number } = {}): ArrayBuffer {
  const buf = new ArrayBuffer(HEADER_BYTES + bins.length);
  const dv = new DataView(buf);
  const magic = opts.magic ?? "QRMS";
  for (let i = 0; i < 4; i++) dv.setUint8(i, magic.charCodeAt(i));
  dv.setUint16(4, opts.version ?? 1, true);
  dv.setUint32(8, 42, true);
  dv.setFloat64(12, 7_100_000, true);
  dv.setFloat32(20, 192_000, true);
  dv.setInt16(24, -120, true);
  dv.setInt16(26, -20, true);
  dv.setUint16(28, bins.length, true);
  new Uint8Array(buf, HEADER_BYTES).set(bins);
  return buf;
}

describe("parseSpectrumFrame", () => {
  it("decodes the header and bins", () => {
    const f = parseSpectrumFrame(frame([0, 128, 255]));
    expect(f).toMatchObject({ seq: 42, centerHz: 7_100_000, spanHz: 192_000, dbMin: -120, dbMax: -20 });
    expect(Array.from(f.bins)).toEqual([0, 128, 255]);
  });
  it("rejects bad magic, version and truncated data", () => {
    expect(() => parseSpectrumFrame(frame([1], { magic: "XXXX" }))).toThrow(/magic/);
    expect(() => parseSpectrumFrame(frame([1], { version: 2 }))).toThrow(/version/);
    expect(() => parseSpectrumFrame(frame([1, 2, 3]).slice(0, HEADER_BYTES + 1))).toThrow(/short/);
  });
});
