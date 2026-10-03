export type StageState = "done" | "partial" | "blocked" | "todo";

export type Stage = {
  key: string;
  label: string;
  state: StageState;
  detail: string;
  blocker: string;
};

export type TimelineSection = {
  label: string;
  start_s: number;
  duration_s: number;
  energy_db: number;
  low_energy_db: number;
};

export type Transition = {
  at_s: number;
  from_label: string;
  to_label: string;
  kind: string;
  energy_change_db: number;
  low_change_db?: number;
};

export type Measure = {
  lufs: number;
  true_peak_dbtp: number;
  crest_factor_db: number;
  lra: number | null;
};

export type TrackProgress = {
  slug: string;
  kind: string;
  kind_reason: string;
  duration_s: number;
  percent: number;
  stages: Stage[];
  measurements: { raw: Measure; final: Measure; final_file: string };
  arrangement: {
    sections: number;
    types: number;
    transitions: Transition[];
    notes: string[];
    timeline: TimelineSection[];
  };
  listening: { listeners: number; preferred: string } | null;
  next_step: string;
};

export type Comparison = { tracks: TrackProgress[]; stage_order: string[] };

export const STATE_COPY: Record<StageState, { mark: string; label: string }> = {
  done: { mark: "✓", label: "Done" },
  partial: { mark: "~", label: "Partial" },
  blocked: { mark: "✗", label: "Blocked" },
  todo: { mark: "·", label: "Not started" },
};

/** A stable colour per section letter, so the two timelines read the same way. */
export function sectionColour(label: string): string {
  const palette = ["#ff9e2c", "#6fb8d8", "#7fd4a0", "#c98bdb", "#e8795f", "#d8c56f"];
  return palette[(label.charCodeAt(0) - 65) % palette.length];
}
