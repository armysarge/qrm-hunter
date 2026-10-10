export class AudioPlayer {
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private gain = 0.8;

  async start(): Promise<void> {
    if (this.ctx) return;
    const ctx = new AudioContext();
    await ctx.audioWorklet.addModule("/pcm-player.js");
    const node = new AudioWorkletNode(ctx, "pcm-player", { outputChannelCount: [1] });
    node.connect(ctx.destination);
    await ctx.resume();
    this.ctx = ctx;
    this.node = node;
    this.setGain(this.gain);
  }

  stop(): void {
    this.node?.disconnect();
    void this.ctx?.close();
    this.ctx = null;
    this.node = null;
  }

  setRate(rate: number): void {
    this.node?.port.postMessage({ type: "rate", rate });
  }

  setGain(gain: number): void {
    this.gain = gain;
    this.node?.port.postMessage({ type: "gain", gain });
  }

  push(buf: ArrayBuffer): void {
    if (!this.node) return;
    const pcm = new Int16Array(buf);
    const samples = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768;
    this.node.port.postMessage({ type: "pcm", samples }, [samples.buffer]);
  }
}
