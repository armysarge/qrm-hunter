import type { View } from "./freq";

/** Tuning this close to the span edge (as a fraction of the span) moves the centre. */
export const EDGE_FRACTION = 0.05;
/** Shortest gap between two centre changes; each one clears the pre-buffer and resets RDS. */
export const MIN_RETUNE_INTERVAL_MS = 150;

export type Range = readonly [number, number];

/** Nearest centre to `centerHz` whose whole span (`rateHz` wide) stays inside the device range. */
export function clampCentre(centerHz: number, rateHz: number, range: Range): number {
  const lo = range[0] + rateHz / 2;
  const hi = range[1] - rateHz / 2;
  if (lo > hi) return (range[0] + range[1]) / 2; // the span is wider than the device range
  return Math.round(Math.min(Math.max(centerHz, lo), hi));
}

/** A new SDR centre for a panned view that sticks out of the current span, or null when the view is
 *  inside the span or the centre cannot move any further (device edge). The view keeps its width. */
export function planPanRetune(view: View, span: View, range: Range): number | null {
  if (view.hiHz <= view.loHz || span.hiHz <= span.loHz) return null;
  if (view.loHz >= span.loHz && view.hiHz <= span.hiHz) return null;
  const rate = span.hiHz - span.loHz;
  const next = clampCentre((view.loHz + view.hiHz) / 2, rate, range);
  return Math.abs(next - (span.loHz + span.hiHz) / 2) < 1 ? null : next;
}

/** A new SDR centre for a tuned frequency in the outer 5 % of the span or beyond it, placed like the
 *  engine places it (tuned frequency a quarter span below the centre); null when no move is needed. */
export function planTuneRetune(tuneHz: number, span: View, range: Range): number | null {
  const rate = span.hiHz - span.loHz;
  if (!(rate > 0) || tuneHz < range[0] || tuneHz > range[1]) return null;
  const margin = rate * EDGE_FRACTION;
  if (tuneHz >= span.loHz + margin && tuneHz <= span.hiHz - margin) return null;
  const next = clampCentre(tuneHz + rate / 4, rate, range);
  return Math.abs(next - (span.loHz + span.hiHz) / 2) < 1 ? null : next;
}

export interface RetuneControllerOptions {
  /** The centre to move to right now, or null when nothing is needed. Evaluated lazily so a long
   *  pan is answered with its latest position, not with every intermediate one. */
  plan(): number | null;
  send(centerHz: number): Promise<unknown>;
  minIntervalMs?: number;
  now?(): number;
}

/** At most one centre change in flight and at least `minIntervalMs` between them; pan movement that
 *  arrives meanwhile is coalesced into the next request. */
export class RetuneController {
  private busy = false;
  private dirty = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private lastStart = -Infinity;
  private readonly minInterval: number;
  private readonly now: () => number;

  constructor(private readonly o: RetuneControllerOptions) {
    this.minInterval = o.minIntervalMs ?? MIN_RETUNE_INTERVAL_MS;
    this.now = o.now ?? (() => performance.now());
  }

  /** True while a centre change is in flight or queued: the view may sit outside the span. */
  get active(): boolean {
    return this.busy || this.timer !== undefined;
  }

  /** The view moved: send a retune if one is needed and allowed now, else when the interval is up. */
  poke(): void {
    this.dirty = true;
    if (this.busy || this.timer !== undefined) return;
    const wait = this.lastStart + this.minInterval - this.now();
    if (wait > 0) {
      this.timer = setTimeout(() => {
        this.timer = undefined;
        void this.run();
      }, wait);
    } else void this.run();
  }

  cancel(): void {
    clearTimeout(this.timer);
    this.timer = undefined;
    this.dirty = false;
  }

  private async run(): Promise<void> {
    if (!this.dirty) return;
    this.dirty = false;
    const centre = this.o.plan();
    if (centre === null) return;
    this.busy = true;
    this.lastStart = this.now();
    try {
      await this.o.send(centre);
    } catch {
      // the caller reports send failures; the next pan movement retries
    } finally {
      this.busy = false;
    }
    if (this.dirty) this.poke();
  }
}
