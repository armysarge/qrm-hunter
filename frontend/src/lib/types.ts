export type Mode = "AM" | "USB" | "LSB" | "CW" | "FM" | "WFM";
export type DirectSampling = "off" | "i" | "q";

export interface DeviceInfo {
  id: string;
  driver: string;
  label: string;
  serial: string;
  freq_range: [number, number];
  sample_rates: number[];
  gain_range: [number, number];
  has_direct_sampling: boolean;
}

export interface RadioState {
  device_id: string | null;
  running: boolean;
  center_hz: number;
  sample_rate: number;
  gain_db: number | null;
  ppm: number;
  direct_sampling: DirectSampling;
  tune_hz: number;
  mode: Mode;
  bw_hz: number;
  fft_size: number;
  avg_alpha: number;
  db_min: number;
  db_max: number;
  fps: number;
  audio: boolean;
}

export type StatePatch = Partial<Omit<RadioState, "device_id" | "running">>;

export interface Stats { dropped: number; overflows: number; frame_rate: number; clients: number; }

export type BandGroup = "HF" | "VHF" | "UHF" | "BC";
export interface Band { name: string; lo_hz: number; hi_hz: number; default_hz: number; group: BandGroup; }

export type ColormapName = "turbo" | "viridis" | "grey";

export interface Settings {
  region: 1 | 2 | 3;
  mains_hz: 50 | 60 | "auto";
  theme: "dark" | "light";
  colormap: ColormapName;
  last_device_id?: string | null;
  last_tune_hz: number;
  prebuffer_s: number;
  recordings_dir: string;
  callsign: string;
}

export type SettingsPatch = Partial<Pick<Settings,
  "region" | "mains_hz" | "theme" | "colormap" | "prebuffer_s" | "recordings_dir" | "callsign">>;

export interface MemoryFields {
  name: string;
  tune_hz: number;
  mode: Mode;
  bw_hz: number;
  gain_db: number | null; // null = AGC
  direct_sampling: DirectSampling | null; // null = leave the radio's setting alone
  note: string;
}

export interface Memory extends MemoryFields { id: string; created: string; }

export type ImportFormat = "json" | "csv";

/** Decoded RDS from a broadcast FM station (GET /api/rds, "rds" events). */
export interface RdsState {
  pi: string | null;
  ps: string | null;
  rt: string | null;
  pty: number | null;
  pty_name: string | null;
  tp: boolean;
  ta: boolean;
  ms: boolean;
  ct: string | null; // station local time, ISO 8601 with its UTC offset
  rtplus: { title: string | null; artist: string | null } | null;
  stereo: boolean;
  synced: boolean;
  block_error_rate: number;
}

export interface RecordingInfo {
  id: string;
  name: string;
  created: string;
  center_hz: number;
  sample_rate: number;
  lo_hz: number;
  hi_hz: number;
  span_hz: number;
  duration_s: number;
  size_bytes: number;
  has_analysis: boolean;
  top_cause: string | null;
  truncated: boolean;
  end_reason: string;
  device_id: string;
  band: string | null;
  callsign: string | null;
}

export interface RecordStatus { recording: boolean; elapsed_s: number; bytes: number; id: string | null; }
export interface RecordRequest { seconds?: number; lo_hz?: number; hi_hz?: number; analysis_id?: string; lead_in_s?: number; }

export interface Cause {
  id: string;
  name: string;
  score: number;
  rule_score: number;
  model_score: number | null;
  evidence: string[];
  hunt: string;
  description: string;
  beta: boolean;
}

export interface AnalysisSource {
  kind: "live" | "recording";
  center_hz: number;
  sample_rate: number;
  lo_hz: number;
  hi_hz: number;
  seconds: number;
  recording_id: string | null;
}

export interface AnalysisResult {
  id: string;
  created: string;
  source: AnalysisSource;
  causes: Cause[];
  features: Record<string, number | null>;
  timing_ms: Record<string, number>;
  versions: Record<string, string>;
  ranker: string;
}

export type AnalyseRequest =
  | { source: "live"; seconds?: number; lo_hz?: number; hi_hz?: number }
  | { source: "recording"; id: string };

export interface AppInfo {
  version: string;
  desktop: boolean;
  recordings_dir: string;
  default_recordings_dir: string;
  ranker: string;
  signatures_warning: string | null;
}

/** Server events. An "error" with code "signatures" reports a user signatures file that could not be loaded. */
export type ServerEvent =
  | { type: "state"; state: RadioState }
  | { type: "rds"; rds: RdsState | null }
  | { type: "error"; code: string; message: string }
  | { type: "stats"; stats: Stats }
  | { type: "memories" }
  | { type: "recording"; elapsed_s: number; bytes: number }
  | { type: "recording_stopped"; reason: string; recording: RecordingInfo | null; error: string | null }
  | { type: "recordings" };
