import type { SpectrumFrame } from "./protocol";

type Listener = (frame: SpectrumFrame) => void;
const listeners = new Set<Listener>();

/** Non-reactive fan-out for 25 fps frames, which deliberately bypasses Svelte reactivity. */
export const frameBus = {
  on(listener: Listener): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  emit(frame: SpectrumFrame): void {
    for (const l of listeners) l(frame);
  },
};
