/** Binary spectrum frame — mirrors backend/qrm_hunter/api/protocol.py (HEADER "<4sHHIdfhhH2x"). */
export interface SpectrumFrame {
  seq: number;
  centerHz: number;
  spanHz: number;
  dbMin: number;
  dbMax: number;
  bins: Uint8Array;
}

export const HEADER_BYTES = 32;

export function parseSpectrumFrame(buf: ArrayBuffer): SpectrumFrame {
  if (buf.byteLength < HEADER_BYTES) throw new Error("spectrum frame too short");
  const dv = new DataView(buf);
  const magic = String.fromCharCode(dv.getUint8(0), dv.getUint8(1), dv.getUint8(2), dv.getUint8(3));
  if (magic !== "QRMS") throw new Error(`bad spectrum frame magic ${JSON.stringify(magic)}`);
  const version = dv.getUint16(4, true);
  if (version !== 1) throw new Error(`unsupported spectrum frame version ${version}`);
  const nBins = dv.getUint16(28, true);
  if (buf.byteLength < HEADER_BYTES + nBins) throw new Error("spectrum frame too short for its bins");
  return {
    seq: dv.getUint32(8, true),
    centerHz: dv.getFloat64(12, true),
    spanHz: dv.getFloat32(20, true),
    dbMin: dv.getInt16(24, true),
    dbMax: dv.getInt16(26, true),
    bins: new Uint8Array(buf, HEADER_BYTES, nBins),
  };
}
