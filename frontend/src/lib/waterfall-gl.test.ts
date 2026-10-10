import { describe, expect, it } from "vitest";
import { WaterfallRenderer } from "./waterfall-gl";

/** A stand-in WebGL2 context: every method is recorded and returns a harmless value. */
function fakeGl() {
  const calls: string[] = [];
  let next = 1;
  const gl = new Proxy({} as Record<string, unknown>, {
    get(_t, prop: string) {
      if (/^[A-Z0-9_]+$/.test(prop)) return prop; // GLenum constants
      return (..._args: unknown[]) => {
        calls.push(prop);
        if (prop === "getParameter") return 4096;
        if (prop.startsWith("get") && prop.endsWith("Parameter")) return true; // compile/link status
        if (prop === "getAttribLocation") return 0;
        return { id: next++ };
      };
    },
  });
  return { gl, calls };
}

function fakeCanvas() {
  const { gl, calls } = fakeGl();
  const canvas = Object.assign(new EventTarget(), {
    width: 300,
    height: 200,
    getContext: () => gl,
  });
  return { canvas: canvas as unknown as HTMLCanvasElement, calls };
}

const lut = new Uint8Array(256 * 4);
const count = (calls: string[], name: string) => calls.filter((c) => c === name).length;

describe("WaterfallRenderer context loss", () => {
  it("prevents default on loss, goes quiet, and rebuilds everything on restore", () => {
    const { canvas, calls } = fakeCanvas();
    const r = new WaterfallRenderer(canvas, lut);
    r.resize(300, 200);
    expect(count(calls, "createProgram")).toBe(1);

    const lost = new Event("webglcontextlost", { cancelable: true });
    canvas.dispatchEvent(lost);
    expect(lost.defaultPrevented).toBe(true);
    expect(r.contextLost).toBe(true);

    const before = calls.length;
    r.pushRow(new Uint8Array(300));
    r.draw();
    r.setColormap(lut);
    r.resize(320, 240); // remembered and applied on restore
    expect(calls.length).toBe(before);

    let restored = 0;
    r.onRestored = () => restored++;
    canvas.dispatchEvent(new Event("webglcontextrestored"));
    expect(r.contextLost).toBe(false);
    expect(restored).toBe(1);
    expect(count(calls, "createProgram")).toBe(2);
    expect(count(calls, "createVertexArray")).toBe(2);
    expect(r.width).toBe(320);

    const n = calls.length;
    r.pushRow(new Uint8Array(320));
    r.draw();
    expect(calls.slice(n)).toContain("texSubImage2D");
    expect(calls.slice(n)).toContain("drawArrays");
  });

  it("stops listening after dispose", () => {
    const { canvas, calls } = fakeCanvas();
    const r = new WaterfallRenderer(canvas, lut);
    r.dispose();
    const lost = new Event("webglcontextlost", { cancelable: true });
    canvas.dispatchEvent(lost);
    expect(lost.defaultPrevented).toBe(false);
    canvas.dispatchEvent(new Event("webglcontextrestored"));
    expect(count(calls, "createProgram")).toBe(1);
  });
});
