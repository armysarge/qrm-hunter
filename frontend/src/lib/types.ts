export type Mode = "AM" | "USB" | "LSB" | "CW" | "FM";
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

export interface Band { name: string; lo_hz: number; hi_hz: number; default_hz: number; }

export type ColormapName = "turbo" | "viridis" | "grey";

export interface Settings {
  region: 1 | 2 | 3;
  mains_hz: 50 | 60 | "auto";
  theme: "dark" | "light";
  colormap: ColormapName;
  last_device_id?: string | null;
  last_tune_hz: number;
}

export type SettingsPatch = Partial<Pick<Settings, "region" | "mains_hz" | "theme" | "colormap">>;

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

export type ServerEvent =
  | { type: "state"; state: RadioState }
  | { type: "error"; code: string; message: string }
  | { type: "stats"; stats: Stats }
  | { type: "memories" };
