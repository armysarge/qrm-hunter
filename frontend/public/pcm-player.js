// AudioWorklet: plays mono Float32 chunks at an arbitrary source rate with a small jitter buffer.
class PcmPlayer extends AudioWorkletProcessor {
  constructor() {
    super();
    this.queue = [];
    this.pos = 0;
    this.buffered = 0;
    this.srcRate = 12000;
    this.gain = 0.8;
    this.started = false;
    this.port.onmessage = (e) => {
      const m = e.data;
      if (m.type === "rate") this.srcRate = m.rate;
      else if (m.type === "gain") this.gain = m.gain;
      else if (m.type === "pcm") {
        this.queue.push(m.samples);
        this.buffered += m.samples.length;
        while (this.buffered > this.srcRate * 0.5 && this.queue.length > 1) {
          this.buffered -= this.queue.shift().length; // too far behind: drop oldest
          this.pos = 0;
        }
      }
    };
  }

  process(_inputs, outputs) {
    const out = outputs[0][0];
    if (!this.started) {
      if (this.buffered < this.srcRate * 0.1) {
        out.fill(0);
        return true;
      }
      this.started = true;
    }
    const step = this.srcRate / sampleRate;
    for (let i = 0; i < out.length; i++) {
      let chunk = this.queue[0];
      while (chunk && this.pos >= chunk.length) {
        this.pos -= chunk.length;
        this.buffered -= chunk.length;
        this.queue.shift();
        chunk = this.queue[0];
      }
      if (!chunk) {
        out.fill(0, i);
        this.started = false; // underrun: rebuffer
        this.pos = 0;
        return true;
      }
      const i0 = Math.floor(this.pos);
      const frac = this.pos - i0;
      const a = chunk[i0];
      const b = i0 + 1 < chunk.length ? chunk[i0 + 1] : (this.queue[1]?.[0] ?? a);
      out[i] = (a + (b - a) * frac) * this.gain;
      this.pos += step;
    }
    return true;
  }
}

registerProcessor("pcm-player", PcmPlayer);
