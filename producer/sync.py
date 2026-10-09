"""`producer sync` — this machine doing what the studio web app was asked for.

The web app is hosted and cannot touch the audio: it records uploads, notes and
settings requests in a store and waits. This is the other half. One pass:

  1. uploads   -> checked by the intake gate, placed in the workspace
  2. requests  -> settings changes validated, then re-rendered
  3. feedback  -> each song's feedback.md rebuilt
  4. publish   -> song.json and web-sized audio pushed back for the UI

It is the only writer of audio and the only place a number is measured, which
keeps the rule the rest of the project runs on: the CLI is the engine.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from producer.benchmark import measure
from producer.feedback import FEEDBACK_MD, write_feedback
from producer.intake import (
    BLOCKED,
    CAUTION,
    INTAKE_SUFFIXES,
    READY,
    check_file,
    slugify,
    standardize,
)
from producer.progress import track_progress
from producer.song import (
    BEAT,
    FIELD_BY_KEY,
    FIELDS,
    GROUP_COPY,
    GROUPS,
    RebuildError,
    SettingsError,
    component_path,
    component_specs,
    effective,
    final_component,
    group_applies,
    groups_touched,
    load_recorded,
    load_title,
    rebuild,
    save_settings,
    validate_changes,
)
from producer.store import Entry, Store
from producer.workspace import ORIGINAL, REFERENCE_STEM, find_track_reference, song_dirs

UPLOAD_KINDS = ("vocal", "beat", "reference", "other")
UPLOAD_LOG = "UPLOAD_LOG.md"
UPLOADS_JSON = "uploads.json"

WEB_AUDIO_BITRATE = 256_000
MEASURE_KEYS = ("lufs", "true_peak_dbtp", "lra", "crest_factor_db", "duration_s",
                "sample_rate", "channels")

Encoder = Callable[[Path, Path], None]
Log = Callable[[str], None]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def encode_aac(source: Path, dest: Path) -> None:
    """AAC for the browser. The lossless file stays here and stays the one measured."""
    if shutil.which("afconvert") is None:
        raise RuntimeError("`afconvert` is unavailable, so audio cannot be encoded for the web.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["afconvert", "-f", "m4af", "-d", "aac", "-b", str(WEB_AUDIO_BITRATE),
         str(source), str(dest)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not dest.exists():
        raise RuntimeError(f"Could not encode {source.name}: {result.stderr.strip() or 'afconvert failed'}")


@dataclass
class SyncSummary:
    """What one pass did, for the terminal and for the tests."""

    uploads: list[dict] = field(default_factory=list)
    requests: list[dict] = field(default_factory=list)
    feedback: dict[str, int] = field(default_factory=dict)
    published: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def did_something(self) -> bool:
        return bool(self.uploads or self.requests or self.published or self.problems)


class Mirror:
    """A local copy of the store's small JSON objects, refetched only when they change."""

    def __init__(self, store: Store, root: Path) -> None:
        self.store = store
        self.root = root
        self.index_path = root / "index.json"
        self.index: dict[str, str] = (
            json.loads(self.index_path.read_text()) if self.index_path.exists() else {}
        )

    def read(self, entry: Entry):
        local = self.root / "objects" / entry.path
        if self.index.get(entry.path) != entry.stamp or not local.exists():
            if not self.store.download(entry.path, local):
                return None
            self.index[entry.path] = entry.stamp
        try:
            return json.loads(local.read_text())
        except json.JSONDecodeError:
            return None

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.index, indent=2, sort_keys=True) + "\n")


class Syncer:
    """One workspace, one store."""

    def __init__(
        self,
        workspace: str | Path,
        store: Store,
        vocals_dir: str | Path = "vocals",
        encode: Encoder = encode_aac,
        log: Log = lambda _msg: None,
    ) -> None:
        self.workspace = Path(workspace)
        self.store = store
        self.vocals_dir = Path(vocals_dir)
        self.encode = encode
        self.log = log
        self.state_dir = self.workspace / ".sync"
        self.mirror = Mirror(store, self.state_dir / "mirror")

    # ── shared helpers ──────────────────────────────────────────────────────

    def _json_under(self, prefix: str) -> dict[str, dict]:
        """Every readable JSON object below `prefix`, keyed by store path."""
        found: dict[str, dict] = {}
        for entry in self.store.list(prefix):
            if entry.path.endswith(".json"):
                value = self.mirror.read(entry)
                if isinstance(value, dict):
                    found[entry.path] = value
        return found

    def _archive(self, path: Path) -> None:
        """Move a file about to be replaced somewhere safe. Nothing is ever deleted."""
        if not path.exists():
            return
        keep = path.parent / ".replaced"
        keep.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.move(str(path), str(keep / f"{stamp}-{path.name}"))

    def _title(self, song_dir: Path) -> str:
        return load_title(song_dir) or self._intake_titles().get(song_dir.name) \
            or song_dir.name.replace("-", " ").title()

    def _intake_titles(self) -> dict[str, str]:
        path = self.workspace / "intake.json"
        if not path.exists():
            return {}
        try:
            rows = json.loads(path.read_text())
        except json.JSONDecodeError:
            return {}
        return {r["slug"]: Path(r["source_file"]).stem for r in rows if "slug" in r}

    # ── 1. uploads ──────────────────────────────────────────────────────────

    def process_uploads(self, summary: SyncSummary) -> None:
        by_id: dict[str, dict[str, Entry]] = {}
        for entry in self.store.list("uploads/"):
            parts = entry.path.split("/")
            if len(parts) == 3:
                by_id.setdefault(parts[1], {})[parts[2]] = entry

        for upload_id, files in sorted(by_id.items()):
            if "report.json" in files or "meta.json" not in files:
                continue
            payload = next((e for name, e in files.items() if name.startswith("file.")), None)
            if payload is None:
                continue                      # still uploading; pick it up next pass
            meta = self.mirror.read(files["meta.json"])
            if not isinstance(meta, dict):
                continue

            report = self._handle_upload(upload_id, meta, payload)
            self.store.put_json(f"uploads/{upload_id}/report.json", report)
            self._record_upload(meta, report)
            summary.uploads.append({**meta, "report": report})
            if report["verdict"] != READY:
                for problem in [*report["blockers"], *report["warnings"]]:
                    summary.problems.append(
                        f"{meta.get('filename', upload_id)} ({report['verdict']}): {problem}"
                    )

    def _handle_upload(self, upload_id: str, meta: dict, payload: Entry) -> dict:
        kind = meta.get("kind") if meta.get("kind") in UPLOAD_KINDS else "other"
        slug = slugify(str(meta.get("song") or "untitled"))
        filename = re.sub(r"[^\w.\- ]+", "_", Path(str(meta.get("filename") or "upload")).name) \
            or "upload"
        report: dict = {
            "id": upload_id, "slug": slug, "kind": kind, "verdict": READY,
            "blockers": [], "warnings": [], "metrics": {}, "placed": None, "kept": None,
            "rendered": [], "checked_at": now_iso(),
        }

        # The file as it arrived is always kept, whatever the verdict.
        kept = self.vocals_dir / slug / filename
        if kept.exists():
            kept = kept.with_name(f"{kept.stem}-{upload_id[:6]}{kept.suffix}")
        if not self.store.download(payload.path, kept):
            report.update(verdict=BLOCKED, blockers=["The uploaded file is missing from storage."])
            return report
        report["kept"] = str(kept)
        self.log(f"upload: {filename} -> {kept}")

        if kind == "other":
            report["warnings"] = []
            report["note"] = (f"Kept in {kept.parent}/. Nothing in the pipeline uses it "
                              "automatically.")
            return report

        if kept.suffix.lower() not in INTAKE_SUFFIXES:
            report.update(verdict=BLOCKED, blockers=[
                f"{filename} is a {kept.suffix or 'file with no extension'} — not an audio "
                f"format this can read. Send WAV, AIFF, FLAC, MP3 or M4A."
            ])
            return report

        scratch = self.state_dir / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        try:
            result, track = check_file(kept, scratch, slug, vocal=(kind == "vocal"))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        report.update(verdict=result.verdict, blockers=list(result.blockers),
                      warnings=list(result.warnings), metrics=result.metrics)
        if track is None or result.verdict == BLOCKED:
            return report

        song_dir = self.workspace / slug
        target = {
            "vocal": song_dir / ORIGINAL,
            "beat": song_dir / BEAT,
            "reference": song_dir / f"{REFERENCE_STEM}.wav",
        }[kind]
        existing = find_track_reference(song_dir) if kind == "reference" else (
            target if target.exists() else None
        )

        if existing is not None and not meta.get("replaces"):
            # An upload from the Upload screen must not silently overwrite a
            # song's vocal. Swapping is asked for explicitly, on the song.
            report["verdict"] = CAUTION
            report["warnings"].append(
                f"{slug} already has a {kind}. This file was kept in {kept.parent}/ and is "
                f"not in use. To swap it in, use Replace file on the song."
            )
            return report

        is_new_song = not (song_dir / ORIGINAL).exists()
        if existing is not None:
            self._archive(existing)
        standardize(track, target)
        report["placed"] = str(target)

        if kind == "vocal" and is_new_song:
            save_settings(song_dir, load_recorded(song_dir),
                          title=str(meta.get("song_title") or meta.get("song") or slug))
            return report
        if not (song_dir / ORIGINAL).exists():
            report["warnings"].append(
                f"There is no vocal for {slug} yet, so this {kind} is waiting in its folder."
            )
            report["verdict"] = CAUTION
            return report

        # A new input makes everything built on the old one out of date.
        start, force = {"vocal": ("vocal", ()), "beat": ("balance", ("balance",)),
                        "reference": ("master", ())}[kind]
        if kind == "reference" and effective(song_dir)["use_reference"] < 0.5:
            return report
        try:
            report["rendered"] = rebuild(song_dir, start, force=force, log=self.log)
        except RebuildError as exc:
            report["verdict"] = CAUTION
            report["warnings"].append(
                f"The file is in place, but re-rendering with it failed: {exc} "
                "Everything built from the previous file is unchanged and now out of date."
            )
        return report

    def _record_upload(self, meta: dict, report: dict) -> None:
        """Keep a durable list of everything uploaded, and regenerate the log from it."""
        path = self.workspace / UPLOADS_JSON
        rows = json.loads(path.read_text()) if path.exists() else []
        rows = [r for r in rows if r.get("id") != report["id"]]
        rows.append({
            "id": report["id"], "filename": meta.get("filename"), "kind": report["kind"],
            "song": report["slug"], "uploader": meta.get("uploader"),
            "uploaded_at": meta.get("uploaded_at"), "replaces": bool(meta.get("replaces")),
            "report": report,
        })
        rows.sort(key=lambda r: (r.get("uploaded_at") or "", r["id"]))
        self.workspace.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, indent=2) + "\n")
        (self.workspace / UPLOAD_LOG).write_text(build_upload_log(rows))

    # ── 2. settings requests ────────────────────────────────────────────────

    def _requests(self) -> dict[str, list[dict]]:
        """Every request, by song, each carrying its result when there is one."""
        objects = self._json_under("requests/")
        by_slug: dict[str, list[dict]] = {}
        for path, request in objects.items():
            if path.endswith(".result.json"):
                continue
            parts = path.split("/")
            if len(parts) != 3:
                continue
            result = objects.get(path[: -len(".json")] + ".result.json")
            merged = {**request, "slug": parts[1], "id": parts[2][: -len(".json")]}
            # "rendering" is a marker, not an outcome: if a pass died mid-render
            # the request is still owed, so it stays pending.
            if result and result.get("state") != "rendering":
                merged["result"] = result
                merged["before"] = result.get("before", {})
            by_slug.setdefault(parts[1], []).append(merged)
        for rows in by_slug.values():
            rows.sort(key=lambda r: (r.get("created_at") or "", r["id"]))
        return by_slug

    def _finish(self, request: dict, result: dict, summary: SyncSummary) -> None:
        result = {**result, "at": now_iso()}
        self.store.put_json(f"requests/{request['slug']}/{request['id']}.result.json", result)
        request["result"] = result
        request["before"] = result.get("before", {})
        summary.requests.append(request)
        if result["state"] != "done":
            summary.problems.append(
                f"{request['slug']}: settings change {result['state']} — {result.get('message', '')}"
            )

    def process_requests(self, summary: SyncSummary) -> None:
        for slug, rows in sorted(self._requests().items()):
            pending = [r for r in rows if "result" not in r]
            if not pending:
                continue
            song_dir = self.workspace / slug
            if not (song_dir / ORIGINAL).exists():
                for request in pending:
                    self._finish(request, {"state": "rejected",
                                           "message": f"There is no song called {slug}."}, summary)
                continue

            before = effective(song_dir)
            merged: dict = {}
            accepted: list[dict] = []
            for request in pending:
                try:
                    clean = validate_changes(request.get("changes"))
                    for group in groups_touched(clean):
                        applies, why_not = group_applies(song_dir, group)
                        if not applies:
                            raise SettingsError(why_not)
                except SettingsError as exc:
                    self._finish(request, {"state": "rejected", "message": str(exc)}, summary)
                    continue
                request["changes"] = clean
                merged.update(clean)        # requests are in order, so the latest wins
                accepted.append(request)
            if not accepted:
                continue

            groups = groups_touched(merged)
            self.log(f"{slug}: re-rendering from {groups[0]} "
                     f"({', '.join(f'{k}={v:g}' for k, v in sorted(merged.items()))})")
            for request in accepted:
                self.store.put_json(
                    f"requests/{slug}/{request['id']}.result.json",
                    {"state": "rendering", "at": now_iso()},
                )
            started = time.monotonic()
            try:
                rendered = rebuild(song_dir, groups[0], values={**before, **merged},
                                   force=tuple(groups), log=self.log)
            except RebuildError as exc:
                for request in accepted:
                    self._finish(request, {
                        "state": "failed", "message": str(exc),
                        "before": {k: before[k] for k in request["changes"]},
                    }, summary)
                continue

            save_settings(song_dir, {**load_recorded(song_dir), **merged})
            took = round(time.monotonic() - started, 1)
            for request in accepted:
                self._finish(request, {
                    "state": "done", "rendered": rendered, "took_s": took,
                    "before": {k: before[k] for k in request["changes"]},
                }, summary)

    # ── 3. feedback ─────────────────────────────────────────────────────────

    def write_feedback_files(self, summary: SyncSummary) -> None:
        notes: dict[str, list[dict]] = {}
        for path, entry in self._json_under("feedback/").items():
            parts = path.split("/")
            if len(parts) == 3:
                notes.setdefault(parts[1], []).append(entry)
        requests = self._requests()

        uploads_path = self.workspace / UPLOADS_JSON
        uploads: dict[str, list[dict]] = {}
        if uploads_path.exists():
            for row in json.loads(uploads_path.read_text()):
                uploads.setdefault(row.get("song", ""), []).append(row)

        field_labels = {f.key: f.label for f in FIELDS}
        for song_dir in song_dirs(self.workspace):
            slug = song_dir.name
            have = notes.get(slug, []), requests.get(slug, []), uploads.get(slug, [])
            if not any(have) and not (song_dir / FEEDBACK_MD).exists():
                continue
            kind = self._core(song_dir)["kind"]
            labels = {spec.key: spec.label for spec in component_specs(kind)}
            write_feedback(song_dir, self._title(song_dir), *have, labels, field_labels)
            summary.feedback[slug] = len([n for n in have[0] if str(n.get("text", "")).strip()])

    # ── 4. publish ──────────────────────────────────────────────────────────

    def _fingerprint(self, song_dir: Path) -> str:
        """Changes whenever anything the song payload is built from does."""
        parts = []
        for path in sorted(song_dir.iterdir()):
            if path.is_file() and not path.name.startswith(".") \
                    and path.name not in {FEEDBACK_MD, "feedback.json"}:
                stat = path.stat()
                parts.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
        return hashlib.sha1("|".join(parts).encode()).hexdigest()

    def _core(self, song_dir: Path) -> dict:
        """The measured part of a song's payload — slow to build, so cached."""
        cache_path = self.state_dir / "songs" / f"{song_dir.name}.json"
        fingerprint = self._fingerprint(song_dir)
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            if cached.get("fingerprint") == fingerprint:
                return cached

        self.log(f"{song_dir.name}: measuring")
        progress = track_progress(song_dir).to_dict()
        kind = progress["kind"]

        components = []
        for spec in component_specs(kind):
            path = component_path(song_dir, spec)
            row = {"key": spec.key, "label": spec.label, "about": spec.about,
                   "replace_as": spec.replace_as, "present": path is not None}
            if path is not None:
                measured = measure(path)
                digest = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
                row.update(
                    file=path.name, hash=digest,
                    audio=f"songs/{song_dir.name}/audio/{spec.key}.{digest}.m4a",
                    measure={k: measured.get(k) for k in MEASURE_KEYS},
                )
            elif not spec.replace_as:
                continue                      # a pipeline output that has not been made
            components.append(row)

        values, recorded = effective(song_dir), load_recorded(song_dir)
        groups = []
        for group in GROUPS:
            if not any(f.group == group for f in FIELDS):
                continue    # text-only groups have no knob the studio can show yet
            applies, why_not = group_applies(song_dir, group, kind)
            label, about = GROUP_COPY[group]
            groups.append({
                "key": group, "label": label, "about": about,
                "applies": applies, "why_not": why_not,
                "fields": [
                    {**f.to_dict(), "value": values[f.key], "recorded": f.key in recorded}
                    for f in FIELDS if f.group == group
                ],
            })

        final_file = progress["measurements"]["final_file"]
        core = {
            "fingerprint": fingerprint,
            "slug": song_dir.name,
            "kind": kind,
            "progress": progress,
            "components": components,
            "final_component": final_component(song_dir),
            "timeline_component": next(
                (c["key"] for c in components if c.get("file") == final_file), "raw"),
            "settings": groups,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(core, indent=2) + "\n")
        return core

    def publish(self, summary: SyncSummary) -> None:
        in_store = {e.path for e in self.store.list("songs/")}
        index = []
        for song_dir in song_dirs(self.workspace):
            slug = song_dir.name
            core = self._core(song_dir)
            song = {**{k: v for k, v in core.items() if k != "fingerprint"},
                    "title": self._title(song_dir), "version": core["fingerprint"][:12]}

            wanted_audio = set()
            for component in song["components"]:
                if not component["present"]:
                    continue
                wanted_audio.add(component["audio"])
                if component["audio"] in in_store:
                    continue
                source = component_path(song_dir, next(
                    s for s in component_specs(song["kind"]) if s.key == component["key"]))
                encoded = self.state_dir / "audio" / slug / Path(component["audio"]).name
                if not encoded.exists():
                    self.log(f"{slug}: encoding {component['file']}")
                    self.encode(source, encoded)
                self.store.upload(component["audio"], encoded, "audio/mp4")

            song_path = f"songs/{slug}/song.json"
            published = self.state_dir / "songs" / f"{slug}.published"
            marker = published.read_text() if published.exists() else ""
            if song_path not in in_store or marker != song["version"] + song["title"]:
                self.store.put_json(song_path, song)
                published.write_text(song["version"] + song["title"])
                summary.published.append(slug)

            # Audio that no component points at any more is a superseded render.
            for stale in in_store:
                if stale.startswith(f"songs/{slug}/audio/") and stale not in wanted_audio:
                    self.store.delete(stale)

            index.append({
                "slug": slug, "title": song["title"], "kind": song["kind"],
                "percent": song["progress"]["percent"],
                "duration_s": song["progress"]["duration_s"],
                "stages": [{"key": s["key"], "label": s["label"], "state": s["state"]}
                           for s in song["progress"]["stages"]],
                "next_step": song["progress"]["next_step"],
                "final_component": song["final_component"],
                "version": song["version"],
            })

        index_path = self.state_dir / "songs" / "index.published"
        serialized = json.dumps(index, sort_keys=True)
        if "songs/index.json" not in in_store or not index_path.exists() \
                or index_path.read_text() != serialized:
            self.store.put_json("songs/index.json", {"songs": index})
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(serialized)

    # ── the pass ────────────────────────────────────────────────────────────

    def run_once(self) -> SyncSummary:
        summary = SyncSummary()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.process_uploads(summary)
        self.process_requests(summary)
        self.write_feedback_files(summary)
        self.publish(summary)
        self.store.put_json("sync/heartbeat.json", {
            "at": now_iso(), "host": socket.gethostname(),
            "songs": len(song_dirs(self.workspace)),
        })
        self.mirror.save()
        return summary


VERDICT_ICON = {READY: "✅", CAUTION: "⚠️", BLOCKED: "⛔"}


def build_upload_log(rows: list[dict]) -> str:
    """Every file anyone has uploaded and what the gate made of it."""
    lines = [
        "# Upload log",
        "",
        "_Written by `producer sync`. Every file that came in through the studio, "
        "newest last, with whatever was wrong with it._",
        "",
    ]
    if not rows:
        return "\n".join(lines + ["Nothing has been uploaded yet.", ""])

    problems = [r for r in rows if r["report"]["verdict"] != READY]
    lines += [f"**{len(rows)} file(s): {len(rows) - len(problems)} fine, "
              f"{len(problems)} with something to look at.**", ""]
    lines += ["| When | File | For | As | By | Verdict |", "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        verdict = row["report"]["verdict"]
        lines.append(
            f"| {(row.get('uploaded_at') or '—')[:16].replace('T', ' ')} | `{row.get('filename')}` "
            f"| {row.get('song')} | {row.get('kind')} | {row.get('uploader') or '—'} "
            f"| {VERDICT_ICON.get(verdict, '')} {verdict} |"
        )
    if problems:
        lines += ["", "## Needs a look", ""]
        for row in problems:
            report = row["report"]
            lines.append(f"**{row.get('filename')}** — {row.get('song')}, {row.get('kind')}, "
                         f"from {row.get('uploader') or 'someone'}")
            lines += [f"- ⛔ {b}" for b in report.get("blockers", [])]
            lines += [f"- ⚠️ {w}" for w in report.get("warnings", [])]
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
