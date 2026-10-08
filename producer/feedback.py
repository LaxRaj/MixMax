"""`feedback.md` — what people said about a song, in one file the project keeps.

Notes are typed in the studio web app and arrive one object per note. This
rebuilds a song's `feedback.md` (for reading) and `feedback.json` (for the rest
of the program) from all of them, together with the settings changes and file
swaps that happened along the way, so the file reads as the song's history
rather than a pile of comments.

The rebuild is deterministic: same entries in, same bytes out. Running sync
twice changes nothing, and the file is never appended to.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

FEEDBACK_MD = "feedback.md"
FEEDBACK_JSON = "feedback.json"


def _when(iso: str | None) -> str:
    """A timestamp short enough to read, in the machine's own time zone."""
    if not iso:
        return "unknown time"
    try:
        stamp = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return stamp.astimezone().strftime("%Y-%m-%d %H:%M")


def _clock(seconds: float) -> str:
    seconds = max(float(seconds), 0.0)
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _quote(text: str) -> list[str]:
    """A note as a blockquote, so line breaks and stray markdown stay inside it."""
    return [f"> {line}" if line.strip() else ">" for line in text.strip().splitlines()]


def _settings_then(entry: dict, field_labels: dict[str, str]) -> str:
    """The settings a note was written against, when they were not the defaults."""
    changed = entry.get("settings_changed") or {}
    if not changed:
        return ""
    parts = [f"{field_labels.get(k, k)} {v:g}" if isinstance(v, (int, float)) else f"{k} {v}"
             for k, v in sorted(changed.items())]
    return "settings then: " + ", ".join(parts)


def _note_block(entry: dict, field_labels: dict[str, str]) -> list[str]:
    edited = entry.get("updated_at") and entry.get("updated_at") != entry.get("created_at")
    head = f"**{entry.get('author') or 'Someone'}** · {_when(entry.get('created_at'))}"
    if edited:
        head += f" (edited {_when(entry.get('updated_at'))})"
    lines = [head, *_quote(entry.get("text", ""))]
    then = _settings_then(entry, field_labels)
    if then:
        lines.append(f"_{then}_")
    lines.append("")
    return lines


def normalize_entries(entries: list[dict]) -> list[dict]:
    """Drop empty notes and put the rest in a stable order."""
    kept = [e for e in entries if isinstance(e, dict) and str(e.get("text", "")).strip()]
    return sorted(kept, key=lambda e: (e.get("created_at") or "", e.get("id") or ""))


def build_feedback_md(
    slug: str,
    title: str,
    entries: list[dict],
    requests: list[dict],
    uploads: list[dict],
    component_labels: dict[str, str],
    field_labels: dict[str, str],
) -> str:
    """The whole history of a song, grouped by what each note is about."""
    entries = normalize_entries(entries)
    requests = sorted(requests, key=lambda r: (r.get("created_at") or "", r.get("id") or ""))
    uploads = sorted(uploads, key=lambda u: (u.get("uploaded_at") or "", u.get("id") or ""))

    lines = [
        f"# Feedback — {title}",
        "",
        f"`{slug}` · {len(entries)} note(s), {len(requests)} settings change(s), "
        f"{len(uploads)} file(s) uploaded.",
        "",
        "_Written by `producer sync` from notes left in the studio. It is rebuilt on "
        "every sync, so edit notes there, not here._",
        "",
    ]

    if not entries and not requests and not uploads:
        lines += ["Nobody has left feedback on this song yet.", ""]
        return "\n".join(lines)

    whole = [e for e in entries if (e.get("target") or {}).get("kind", "song") == "song"]
    if whole:
        lines += ["## The whole song", ""]
        for entry in whole:
            lines += _note_block(entry, field_labels)

    by_component: dict[str, list[dict]] = {}
    for entry in entries:
        target = entry.get("target") or {}
        if target.get("kind") == "component":
            by_component.setdefault(target.get("component", "unknown"), []).append(entry)
    if by_component:
        lines += ["## By component", ""]
        ordered = [k for k in component_labels if k in by_component]
        ordered += sorted(k for k in by_component if k not in component_labels)
        for key in ordered:
            lines += [f"### {component_labels.get(key, key)}", ""]
            for entry in by_component[key]:
                lines += _note_block(entry, field_labels)

    sections = [e for e in entries if (e.get("target") or {}).get("kind") == "section"]
    if sections:
        lines += ["## By section", ""]
        sections.sort(key=lambda e: (float(e["target"].get("start_s", 0.0)),
                                     e.get("created_at") or "", e.get("id") or ""))
        heading = None
        for entry in sections:
            target = entry["target"]
            start = float(target.get("start_s", 0.0))
            end = float(target.get("end_s", start))
            where = component_labels.get(target.get("component", ""), "")
            this = (f"### Section {target.get('label', '?')} · {_clock(start)}–{_clock(end)}"
                    + (f" · {where}" if where else ""))
            if this != heading:
                lines += [this, ""]
                heading = this
            lines += _note_block(entry, field_labels)

    if requests:
        lines += ["## Settings changes", ""]
        for request in requests:
            changes = request.get("changes") or {}
            before = request.get("before") or {}
            parts = []
            for key in sorted(changes):
                label = field_labels.get(key, key)
                if key in before:
                    parts.append(f"{label} {before[key]:g} → {changes[key]:g}")
                else:
                    parts.append(f"{label} → {changes[key]:g}")
            result = request.get("result") or {}
            state = result.get("state", "queued")
            line = (f"- {_when(request.get('created_at'))} — **{request.get('author') or 'Someone'}** "
                    f"asked for {', '.join(parts) or 'no change'}. ")
            if state == "done":
                rendered = ", ".join(f"`{name}`" for name in result.get("rendered", []))
                line += f"**Done** — re-rendered {rendered or 'nothing'}."
            elif state in {"failed", "rejected"}:
                line += f"**{state.capitalize()}** — {result.get('message', 'no reason given')}"
            else:
                line += "_Waiting for the studio Mac._"
            lines.append(line)
            if request.get("note"):
                lines += [f"  {quoted}" for quoted in _quote(request["note"])]
        lines.append("")

    if uploads:
        lines += ["## Files", ""]
        for upload in uploads:
            report = upload.get("report") or {}
            verdict = report.get("verdict", "awaiting check")
            line = (f"- {_when(upload.get('uploaded_at'))} — **{upload.get('uploader') or 'Someone'}** "
                    f"uploaded `{upload.get('filename', '?')}` as {upload.get('kind', 'a file')}"
                    f"{' (replacing the current one)' if upload.get('replaces') else ''}: "
                    f"**{verdict}**.")
            if report.get("placed"):
                line += f" Now `{report['placed']}`."
            lines.append(line)
            for problem in [*report.get("blockers", []), *report.get("warnings", [])]:
                lines.append(f"  - {problem}")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def write_feedback(
    song_dir: str | Path,
    title: str,
    entries: list[dict],
    requests: list[dict],
    uploads: list[dict],
    component_labels: dict[str, str],
    field_labels: dict[str, str],
) -> Path:
    """Rebuild `feedback.md` and `feedback.json` for one song."""
    song_dir = Path(song_dir)
    song_dir.mkdir(parents=True, exist_ok=True)
    entries = normalize_entries(entries)
    markdown = build_feedback_md(
        song_dir.name, title, entries, requests, uploads, component_labels, field_labels
    )
    (song_dir / FEEDBACK_MD).write_text(markdown)
    (song_dir / FEEDBACK_JSON).write_text(json.dumps({
        "slug": song_dir.name,
        "notes": entries,
        "requests": sorted(requests, key=lambda r: (r.get("created_at") or "", r.get("id") or "")),
        "uploads": sorted(uploads, key=lambda u: (u.get("uploaded_at") or "", u.get("id") or "")),
    }, indent=2) + "\n")
    return song_dir / FEEDBACK_MD


def load_feedback(song_dir: str | Path) -> dict:
    """Everything said about a song, for any stage of the program that wants it."""
    path = Path(song_dir) / FEEDBACK_JSON
    if not path.exists():
        return {"slug": Path(song_dir).name, "notes": [], "requests": [], "uploads": []}
    return json.loads(path.read_text())
