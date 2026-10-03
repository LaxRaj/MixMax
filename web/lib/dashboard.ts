export type Provenance = "published" | "measured" | "fitted" | "reported" | "guessed";

export type Evidence = {
  name: string;
  value: string;
  provenance: Provenance;
  detail: string;
  source: string;
};

export type Stage = {
  key: string;
  label: string;
  done: number;
  total: number;
  blurb: string;
};

export type Conformance = {
  standard: string;
  target_lufs: number;
  measured_lufs: number;
  normalization_gain_db: number;
  delivered_lufs: number;
  true_peak_after_norm_dbtp: number;
  conforms: boolean;
  issues: string[];
};

export type TrackState = {
  slug: string;
  versions: string[];
  has_ours: boolean;
  has_reference: boolean;
  competitors: string[];
  measurement?: Record<string, number | null>;
  band_balance_db?: Record<string, number>;
  conformance?: Conformance[];
  consequences?: string[];
};

export type Dashboard = {
  stages: Stage[];
  tracks: TrackState[];
  evidence: Evidence[];
  evidence_summary: { counts: Partial<Record<Provenance, number>>; total: number; grounded_pct: number };
  library: null | {
    count: number;
    lufs: Record<string, number>;
    crest_factor_db: Record<string, number>;
    tempo_bpm: Record<string, number>;
    band_balance_db: Record<string, number>;
    kinds: Record<string, number>;
  };
  listening: null | { listeners: string[]; by_source: Record<string, { mean_score: number | null; wins: number }> };
};

export const PROVENANCE_ORDER: Provenance[] = ["published", "measured", "fitted", "reported", "guessed"];

export const PROVENANCE_COPY: Record<Provenance, { label: string; blurb: string }> = {
  published: { label: "Published", blurb: "From a platform or standards body's own document." },
  measured: { label: "Measured", blurb: "Derived from the corpus of real releases." },
  fitted: { label: "Fitted", blurb: "Optimised against a measured target." },
  reported: { label: "Reported", blurb: "Widely reported, but not confirmed at a primary source." },
  guessed: { label: "Guessed", blurb: "A default nobody has checked against anything." },
};
