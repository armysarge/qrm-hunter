// Pure helpers for the RDS card and the traffic-announcement snackbar.
import type { RdsState } from "./types";

/** "Artist – Title" from RT+ when both are tagged, else the RadioText, else whatever RT+ has. */
export function nowPlaying(rds: RdsState | null): string | null {
  if (!rds) return null;
  const artist = rds.rtplus?.artist?.trim();
  const title = rds.rtplus?.title?.trim();
  if (artist && title) return `${artist} – ${title}`;
  return rds.rt?.trim() || title || artist || null;
}

/** Station clock time as "HH:MM" plus its UTC offset ("+02:00"), from the ISO string the engine sends. */
export function formatClock(ct: string | null): string | null {
  const m = ct?.match(/^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/);
  if (!m) return null;
  return `${m[1]} ${m[2] === "Z" || m[2] === "+00:00" ? "UTC" : `UTC${m[2]}`}`;
}

export interface TaSnack { ps: string | null; }

/** The traffic-announcement snackbar after an RDS update. It appears when TA goes from off to on
 *  while TP is set, stays while TA is on (a dismissed one stays dismissed), and goes when TA clears. */
export function taSnackbar(prev: RdsState | null, next: RdsState | null, current: TaSnack | null): TaSnack | null {
  if (!next?.ta) return null;
  if (current) return { ps: next.ps ?? current.ps };
  if (next.tp && !prev?.ta) return { ps: next.ps };
  return null;
}

export const taMessage = (s: TaSnack) => (s.ps?.trim() ? `Traffic announcement — ${s.ps.trim()}` : "Traffic announcement");
