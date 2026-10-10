import type { ColormapName } from "./types";

const VIRIDIS = [
  [68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142],
  [31, 158, 137], [53, 183, 121], [110, 206, 88], [181, 222, 43], [253, 231, 37],
];

function turbo(x: number): [number, number, number] {
  // Google's polynomial approximation of the Turbo colormap
  const r = 0.13572138 + x * (4.6153926 + x * (-42.66032258 + x * (132.13108234 + x * (-152.94239396 + x * 59.28637943))));
  const g = 0.09140261 + x * (2.19418839 + x * (4.84296658 + x * (-14.18503333 + x * (4.27729857 + x * 2.82956604))));
  const b = 0.1066733 + x * (12.64194608 + x * (-60.58204836 + x * (110.36276771 + x * (-89.90310912 + x * 27.34824973))));
  return [r, g, b];
}

function viridis(x: number): [number, number, number] {
  const pos = x * (VIRIDIS.length - 1);
  const i = Math.min(VIRIDIS.length - 2, Math.floor(pos));
  const f = pos - i;
  const a = VIRIDIS[i], b = VIRIDIS[i + 1];
  return [0, 1, 2].map((k) => (a[k] + (b[k] - a[k]) * f) / 255) as [number, number, number];
}

export function buildLut(name: ColormapName): Uint8Array {
  const lut = new Uint8Array(256 * 4);
  for (let i = 0; i < 256; i++) {
    const x = i / 255;
    const [r, g, b] = name === "turbo" ? turbo(x) : name === "viridis" ? viridis(x) : [x, x, x];
    lut.set([r, g, b].map((c) => Math.round(Math.min(1, Math.max(0, c)) * 255)), i * 4);
    lut[i * 4 + 3] = 255;
  }
  return lut;
}
