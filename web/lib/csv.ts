export type LabelResponse = { label: string; score: number | null; notes: string };

export type Submission = {
  slug: string;
  listener: string;
  headphones: boolean;
  responses: LabelResponse[];
  ranking: string[];
  switchCount: number;
};

function escapeCell(value: string): string {
  return /[",\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

/**
 * Emits exactly the schema `producer tally --responses` already reads, so a
 * downloaded file drops straight into the existing CLI with no conversion.
 */
export function toCsv(submission: Submission): string {
  const header = ["listener", "label", "release_ready_1_5", "rank", "notes"];
  const rows = submission.responses.map((r) => {
    const rank = submission.ranking.indexOf(r.label) + 1;
    return [
      submission.listener,
      r.label,
      r.score === null ? "" : String(r.score),
      rank > 0 ? String(rank) : "",
      r.notes,
    ].map(escapeCell);
  });
  return [header, ...rows].map((cells) => cells.join(",")).join("\n") + "\n";
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
