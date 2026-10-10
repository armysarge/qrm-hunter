const VS = `#version 300 es
in vec2 a_pos;
out vec2 v_uv;
void main() { v_uv = a_pos * 0.5 + 0.5; gl_Position = vec4(a_pos, 0.0, 1.0); }`;

const FS = `#version 300 es
precision highp float;
uniform sampler2D u_data;
uniform sampler2D u_lut;
uniform float u_head;
in vec2 v_uv;
out vec4 outColor;
void main() {
  float y = fract(u_head - (1.0 - v_uv.y) + 1.0);
  float v = texture(u_data, vec2(v_uv.x, y)).r;
  outColor = texture(u_lut, vec2(v * (255.0 / 256.0) + 0.5 / 256.0, 0.5));
}`;

function compile(gl: WebGL2RenderingContext, type: number, src: string): WebGLShader {
  const s = gl.createShader(type)!;
  gl.shaderSource(s, src);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s) ?? "shader error");
  return s;
}

function makeTexture(gl: WebGL2RenderingContext): WebGLTexture {
  const t = gl.createTexture()!;
  gl.bindTexture(gl.TEXTURE_2D, t);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  return t;
}

/** Scrolling waterfall: one R8 texture used as a ring of rows, coloured through a 256-entry LUT.
 *  Each new frame costs one texSubImage2D row upload, independent of history length.
 *
 *  Survives WebGL context loss (GPU driver reset, sleep/resume on Windows): `webglcontextlost` is
 *  default-prevented so the browser may restore the context, every call is a no-op while it is
 *  lost, and on `webglcontextrestored` all GPU resources are rebuilt (history starts empty) and
 *  `onRestored` is called so the owner can redraw. */
export class WaterfallRenderer {
  private gl: WebGL2RenderingContext;
  private program!: WebGLProgram;
  private vao!: WebGLVertexArrayObject;
  private lutTex!: WebGLTexture;
  private dataTex: WebGLTexture | null = null;
  private uHead!: WebGLUniformLocation;
  private lut: Uint8Array;
  private head = 0;
  private w = 0;
  private h = 0;
  private reqW = 0;
  private reqH = 0;
  private lost = false;
  onRestored: (() => void) | null = null;

  constructor(private canvas: HTMLCanvasElement, lut: Uint8Array) {
    const gl = canvas.getContext("webgl2", { antialias: false, alpha: false });
    if (!gl) throw new Error("WebGL2 is not available, so the waterfall cannot be shown.");
    this.gl = gl;
    this.lut = lut;
    this.build();
    canvas.addEventListener("webglcontextlost", this.handleLost);
    canvas.addEventListener("webglcontextrestored", this.handleRestored);
  }

  get width(): number {
    return this.w;
  }

  get contextLost(): boolean {
    return this.lost;
  }

  private handleLost = (e: Event): void => {
    e.preventDefault(); // without this the browser never fires webglcontextrestored
    this.lost = true;
    this.dataTex = null; // GPU objects of a lost context are invalid; nothing to delete
  };

  private handleRestored = (): void => {
    this.lost = false;
    this.build();
    if (this.reqW > 0 && this.reqH > 0) this.resize(this.reqW, this.reqH);
    this.onRestored?.();
  };

  /** Create the program, quad and LUT texture (on construction and after a context restore). */
  private build(): void {
    const gl = this.gl;
    const program = gl.createProgram()!;
    gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VS));
    gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program) ?? "link error");
    this.program = program;
    this.vao = gl.createVertexArray()!;
    gl.bindVertexArray(this.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(program, "a_pos");
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.useProgram(program);
    gl.uniform1i(gl.getUniformLocation(program, "u_data"), 0);
    gl.uniform1i(gl.getUniformLocation(program, "u_lut"), 1);
    this.uHead = gl.getUniformLocation(program, "u_head")!;
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.activeTexture(gl.TEXTURE1);
    this.lutTex = makeTexture(gl);
    this.setColormap(this.lut);
  }

  resize(w: number, h: number): void {
    this.reqW = w;
    this.reqH = h;
    if (this.lost) return; // applied on restore
    const gl = this.gl;
    const max = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number;
    this.w = Math.min(w, max);
    this.h = Math.min(h, max);
    gl.activeTexture(gl.TEXTURE0);
    if (this.dataTex) gl.deleteTexture(this.dataTex);
    this.dataTex = makeTexture(gl);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, this.w, this.h, 0, gl.RED, gl.UNSIGNED_BYTE, null);
    this.head = 0;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
  }

  setColormap(lut: Uint8Array): void {
    this.lut = lut;
    if (this.lost) return;
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.lutTex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, lut);
  }

  pushRow(row: Uint8Array): void {
    if (this.lost || !this.dataTex || row.length !== this.w) return;
    const gl = this.gl;
    this.head = (this.head + 1) % this.h;
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.dataTex);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, this.head, this.w, 1, gl.RED, gl.UNSIGNED_BYTE, row);
  }

  draw(): void {
    if (this.lost || !this.dataTex) return;
    const gl = this.gl;
    gl.useProgram(this.program);
    gl.bindVertexArray(this.vao);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.dataTex);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.lutTex);
    gl.uniform1f(this.uHead, (this.head + 0.5) / this.h);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  dispose(): void {
    this.canvas.removeEventListener("webglcontextlost", this.handleLost);
    this.canvas.removeEventListener("webglcontextrestored", this.handleRestored);
    this.onRestored = null;
    if (this.lost) return;
    const gl = this.gl;
    if (this.dataTex) gl.deleteTexture(this.dataTex);
    gl.deleteTexture(this.lutTex);
    gl.deleteProgram(this.program);
  }
}
