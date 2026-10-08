"""`producer sync` — the studio Mac doing what the web app was asked for."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from producer.feedback import build_feedback_md, load_feedback
from producer.song import (
    MASTER,
    VOCAL_MIXED,
    WITH_BEAT,
    RebuildError,
    SettingsError,
    effective,
    load_recorded,
    rebuild,
    validate_changes,
)
from producer.store import LocalStore, StoreError
from producer.sync import Syncer

SR = 22050


def _vocal(seconds: float = 20.0, amp: float = 0.45) -> np.ndarray:
    """A voice: no sub, stops between phrases."""
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    phase = np.cumsum(2 * np.pi * (200 + 40 * np.sin(2 * np.pi * 1.3 * t)) / SR)
    tone = amp * (np.sin(phase) + 0.3 * np.sin(2 * phase)) / 1.3
    gate = ((t % 4.0) < 2.8).astype(np.float32)
    return (tone * gate).astype(np.float32)


def _beat(seconds: float = 20.0) -> np.ndarray:
    rng = np.random.default_rng(2)
    t = np.linspace(0, seconds, int(SR * seconds), endpoint=False)
    out = 0.35 * np.sin(2 * np.pi * 55 * t)
    env = np.exp(-np.linspace(0, 8, int(0.2 * SR)))
    kick = np.sin(2 * np.pi * 55 * np.arange(len(env)) / SR) * env
    for start in range(0, len(out) - len(kick), int(SR * 60.0 / 90.0)):
        out[start:start + len(kick)] += kick * 0.9
    out = out + rng.normal(0, 0.002, len(t))
    return (out / (np.max(np.abs(out)) + 1e-9) * 0.5).astype(np.float32)


def _fake_encode(source: Path, dest: Path) -> None:
    """Encoding is afconvert's job; the tests only care that something is published."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)


@pytest.fixture()
def studio(tmp_path: Path):
    """A workspace with one vocal-and-beat song, a store, and a syncer over both."""
    workspace = tmp_path / "comparisons"
    song = workspace / "night-drive"
    song.mkdir(parents=True)
    sf.write(str(song / "original.wav"), _vocal(), SR)
    sf.write(str(song / "beat.wav"), _beat(), SR)
    store = LocalStore(tmp_path / "store")
    syncer = Syncer(workspace, store, tmp_path / "vocals", encode=_fake_encode)
    return workspace, store, syncer


def _upload(store: LocalStore, tmp_path: Path, upload_id: str, samples: np.ndarray,
            name: str = "take.wav", **meta) -> None:
    source = tmp_path / f"{upload_id}-{name}"
    if name.endswith(".wav"):
        sf.write(str(source), samples, SR)
    else:
        source.write_bytes(b"not audio at all")
    store.upload(f"uploads/{upload_id}/file.{name.rsplit('.', 1)[-1]}", source, "audio/wav")
    store.put_json(f"uploads/{upload_id}/meta.json", {
        "id": upload_id, "filename": name, "kind": "vocal", "song": "new-one",
        "replaces": False, "uploader": "Asha", "uploaded_at": "2026-10-07T10:00:00.000Z",
        **meta,
    })


# ── settings ────────────────────────────────────────────────────────────────


def test_a_value_out_of_range_is_refused_whole() -> None:
    with pytest.raises(SettingsError, match="Loudness must be between"):
        validate_changes({"target_lufs": -2.0, "duck_db": 3.0})


def test_an_unknown_setting_is_refused() -> None:
    with pytest.raises(SettingsError, match="not a setting"):
        validate_changes({"make_it_good": 11})


def test_an_empty_request_is_refused() -> None:
    with pytest.raises(SettingsError):
        validate_changes({})


def test_defaults_are_not_recorded_until_someone_chooses(studio) -> None:
    workspace, _store, _syncer = studio
    song = workspace / "night-drive"
    assert load_recorded(song) == {}
    assert effective(song)["target_lufs"] == -16.0


# ── rebuild ─────────────────────────────────────────────────────────────────


def test_a_balance_change_rerenders_the_mix_but_not_the_vocal(studio) -> None:
    workspace, _store, _syncer = studio
    song = workspace / "night-drive"
    rebuild(song, "vocal", force=("vocal", "balance", "master"))
    vocal_before = (song / VOCAL_MIXED).stat().st_mtime_ns
    mix_before = (song / WITH_BEAT).read_bytes()

    rendered = rebuild(song, "balance", values={"vocal_over_beat_db": 9.0}, force=("balance",))

    assert rendered == [WITH_BEAT, MASTER]
    assert (song / VOCAL_MIXED).stat().st_mtime_ns == vocal_before
    assert (song / WITH_BEAT).read_bytes() != mix_before


def test_a_stage_nobody_asked_for_is_not_conjured(studio) -> None:
    workspace, _store, _syncer = studio
    song = workspace / "night-drive"
    rendered = rebuild(song, "vocal", force=("vocal",))
    assert rendered == [VOCAL_MIXED]
    assert not (song / MASTER).exists()


def test_a_failed_rebuild_leaves_the_song_untouched(studio) -> None:
    workspace, _store, _syncer = studio
    song = workspace / "night-drive"
    rebuild(song, "vocal", force=("vocal",))
    before = (song / VOCAL_MIXED).read_bytes()

    # Twenty seconds of one texture has nothing to build a bridge from.
    with pytest.raises(RebuildError):
        rebuild(song, "vocal", values={"comp_ratio": 8.0}, force=("vocal", "arrangement"))

    assert (song / VOCAL_MIXED).read_bytes() == before
    assert not (song / ".render").exists()


# ── uploads ─────────────────────────────────────────────────────────────────


def test_a_clean_vocal_becomes_a_new_song(studio, tmp_path: Path) -> None:
    workspace, store, syncer = studio
    _upload(store, tmp_path, "upload-aaa", _vocal(12.0), song="Slow Burn", song_title="Slow Burn")

    summary = syncer.run_once()

    report = store.get_json("uploads/upload-aaa/report.json")
    assert report["verdict"] in {"ready", "caution"}
    assert (workspace / "slow-burn" / "original.wav").exists()
    assert (tmp_path / "vocals" / "slow-burn" / "take.wav").exists()
    assert "slow-burn" in summary.published
    assert store.get_json("songs/slow-burn/song.json")["title"] == "Slow Burn"


def test_a_clipped_upload_is_blocked_and_gets_no_song(studio, tmp_path: Path) -> None:
    workspace, store, syncer = studio
    clipped = np.clip(_vocal(12.0) * 6.0, -1.0, 1.0)
    _upload(store, tmp_path, "upload-bbb", clipped, song="Too Hot")

    summary = syncer.run_once()

    report = store.get_json("uploads/upload-bbb/report.json")
    assert report["verdict"] == "blocked"
    assert any("clipped" in b for b in report["blockers"])
    assert not (workspace / "too-hot").exists()
    assert any("take.wav" in p for p in summary.problems)
    log = (workspace / "UPLOAD_LOG.md").read_text()
    assert "Needs a look" in log and "clipped" in log


def test_a_file_that_is_not_audio_is_reported_not_crashed_on(studio, tmp_path: Path) -> None:
    _workspace, store, syncer = studio
    _upload(store, tmp_path, "upload-ccc", np.zeros(1), name="notes.pdf", song="night-drive")

    syncer.run_once()

    report = store.get_json("uploads/upload-ccc/report.json")
    assert report["verdict"] == "blocked"
    assert "not an audio format" in report["blockers"][0]


def test_an_upload_does_not_overwrite_a_vocal_unless_asked(studio, tmp_path: Path) -> None:
    workspace, store, syncer = studio
    original = (workspace / "night-drive" / "original.wav").read_bytes()
    _upload(store, tmp_path, "upload-ddd", _vocal(12.0), song="night-drive")

    syncer.run_once()

    report = store.get_json("uploads/upload-ddd/report.json")
    assert report["verdict"] == "caution"
    assert "already has a vocal" in report["warnings"][-1]
    assert (workspace / "night-drive" / "original.wav").read_bytes() == original


def test_replacing_a_vocal_keeps_the_old_one(studio, tmp_path: Path) -> None:
    workspace, store, syncer = studio
    song = workspace / "night-drive"
    original = (song / "original.wav").read_bytes()
    _upload(store, tmp_path, "upload-eee", _vocal(14.0), song="night-drive", replaces=True)

    syncer.run_once()

    assert (song / "original.wav").read_bytes() != original
    kept = list((song / ".replaced").glob("*-original.wav"))
    assert len(kept) == 1 and kept[0].read_bytes() == original


def test_an_upload_is_only_checked_once(studio, tmp_path: Path) -> None:
    _workspace, store, syncer = studio
    _upload(store, tmp_path, "upload-fff", _vocal(12.0), song="Once")
    assert len(syncer.run_once().uploads) == 1
    assert syncer.run_once().uploads == []


def test_a_half_finished_upload_is_left_alone(studio, tmp_path: Path) -> None:
    _workspace, store, syncer = studio
    source = tmp_path / "partial.wav"
    sf.write(str(source), _vocal(12.0), SR)
    store.upload("uploads/upload-ggg/file.wav", source, "audio/wav")   # no meta yet
    assert syncer.run_once().uploads == []
    assert store.get_json("uploads/upload-ggg/report.json") is None


# ── requests ────────────────────────────────────────────────────────────────


def _request(store: LocalStore, request_id: str, changes: dict, slug: str = "night-drive") -> None:
    store.put_json(f"requests/{slug}/{request_id}.json", {
        "id": request_id, "slug": slug, "author": "Asha",
        "created_at": f"2026-10-07T11:00:0{request_id[-1]}.000Z", "changes": changes,
    })


def test_a_request_rerenders_and_records_the_setting(studio) -> None:
    workspace, store, syncer = studio
    _request(store, "request-1", {"vocal_over_beat_db": 6.0})

    summary = syncer.run_once()

    result = store.get_json("requests/night-drive/request-1.result.json")
    assert result["state"] == "done"
    assert WITH_BEAT in result["rendered"]
    assert result["before"] == {"vocal_over_beat_db": 4.0}
    assert load_recorded(workspace / "night-drive") == {"vocal_over_beat_db": 6.0}
    assert summary.problems == []
    # The published song now shows the value as chosen, not assumed.
    song = store.get_json("songs/night-drive/song.json")
    field = next(f for g in song["settings"] for f in g["fields"] if f["key"] == "vocal_over_beat_db")
    assert field["value"] == 6.0 and field["recorded"] is True


def test_an_out_of_range_request_is_rejected_and_changes_nothing(studio) -> None:
    workspace, store, syncer = studio
    _request(store, "request-2", {"target_lufs": 3.0})

    summary = syncer.run_once()

    result = store.get_json("requests/night-drive/request-2.result.json")
    assert result["state"] == "rejected"
    assert "between" in result["message"]
    assert load_recorded(workspace / "night-drive") == {}
    assert not (workspace / "night-drive" / MASTER).exists()
    assert summary.problems


def test_a_request_for_a_missing_song_is_rejected(studio) -> None:
    _workspace, store, syncer = studio
    _request(store, "request-3", {"target_lufs": -14.0}, slug="nowhere")
    syncer.run_once()
    assert store.get_json("requests/nowhere/request-3.result.json")["state"] == "rejected"


def test_a_request_is_only_applied_once(studio) -> None:
    _workspace, store, syncer = studio
    _request(store, "request-4", {"target_lufs": -14.0})
    assert len(syncer.run_once().requests) == 1
    assert syncer.run_once().requests == []


# ── feedback ────────────────────────────────────────────────────────────────


def _note(store: LocalStore, note_id: str, text: str, target: dict, at: str = "10") -> None:
    store.put_json(f"feedback/night-drive/{note_id}.json", {
        "id": note_id, "slug": "night-drive", "target": target, "author": "Asha", "text": text,
        "created_at": f"2026-10-07T{at}:00:00.000Z", "updated_at": f"2026-10-07T{at}:00:00.000Z",
    })


def test_notes_land_in_feedback_md_grouped_by_what_they_are_about(studio) -> None:
    workspace, store, syncer = studio
    _note(store, "note-aaaa", "Love the hook.", {"kind": "song"}, "10")
    _note(store, "note-bbbb", "Vocal is too dry.", {"kind": "component", "component": "raw"}, "11")
    _note(store, "note-cccc", "This part drags.",
          {"kind": "section", "component": "raw", "label": "B", "index": 1,
           "start_s": 110.0, "end_s": 130.0}, "12")

    summary = syncer.run_once()

    text = (workspace / "night-drive" / "feedback.md").read_text()
    assert summary.feedback == {"night-drive": 3}
    assert text.index("## The whole song") < text.index("## By component") < text.index("## By section")
    assert "> Love the hook." in text
    assert "### Raw vocal" in text and "> Vocal is too dry." in text
    assert "### Section B · 1:50–2:10" in text
    assert "**Asha**" in text
    assert len(load_feedback(workspace / "night-drive")["notes"]) == 3


def test_feedback_md_is_stable_across_syncs(studio) -> None:
    workspace, store, syncer = studio
    _note(store, "note-dddd", "Keep this.", {"kind": "song"})
    syncer.run_once()
    first = (workspace / "night-drive" / "feedback.md").read_bytes()
    syncer.run_once()
    assert (workspace / "night-drive" / "feedback.md").read_bytes() == first


def test_an_edited_note_replaces_itself_and_a_deleted_one_goes(studio) -> None:
    workspace, store, syncer = studio
    _note(store, "note-eeee", "First thought.", {"kind": "song"})
    syncer.run_once()
    _note(store, "note-eeee", "Better thought.", {"kind": "song"})
    syncer.run_once()
    text = (workspace / "night-drive" / "feedback.md").read_text()
    assert "Better thought." in text and "First thought." not in text

    store.delete("feedback/night-drive/note-eeee.json")
    syncer.run_once()
    assert "Better thought." not in (workspace / "night-drive" / "feedback.md").read_text()


def test_settings_changes_and_uploads_are_part_of_the_history(studio, tmp_path: Path) -> None:
    workspace, store, syncer = studio
    _request(store, "request-5", {"duck_db": 4.0})
    _upload(store, tmp_path, "upload-hhh", _vocal(12.0), song="night-drive")
    syncer.run_once()

    text = (workspace / "night-drive" / "feedback.md").read_text()
    assert "## Settings changes" in text and "Beat ducking 2.5 → 4" in text and "**Done**" in text
    assert "## Files" in text and "`take.wav`" in text


def test_a_song_with_no_feedback_gets_no_file(studio) -> None:
    workspace, _store, syncer = studio
    syncer.run_once()
    assert not (workspace / "night-drive" / "feedback.md").exists()


def test_note_text_cannot_break_out_of_its_quote() -> None:
    text = build_feedback_md(
        "s", "S",
        [{"id": "n", "author": "A", "text": "# Not a heading\n\n## Nor this",
          "created_at": "2026-10-07T10:00:00Z", "target": {"kind": "song"}}],
        [], [], {}, {},
    )
    assert "\n# Not a heading" not in text and "> # Not a heading" in text


# ── publish ─────────────────────────────────────────────────────────────────


def test_publish_gives_the_ui_every_component_and_a_heartbeat(studio) -> None:
    _workspace, store, syncer = studio
    summary = syncer.run_once()

    assert summary.published == ["night-drive"]
    song = store.get_json("songs/night-drive/song.json")
    present = {c["key"] for c in song["components"] if c["present"]}
    assert present == {"raw", "beat"}
    # An input that is missing is still offered, so it can be added.
    assert any(c["key"] == "reference" and not c["present"] for c in song["components"])
    for component in song["components"]:
        if component["present"]:
            assert store.get_bytes(component["audio"]) is not None
    assert store.get_json("songs/index.json")["songs"][0]["slug"] == "night-drive"
    assert store.get_json("sync/heartbeat.json")["songs"] == 1


def test_an_unchanged_song_is_not_republished(studio) -> None:
    _workspace, _store, syncer = studio
    syncer.run_once()
    assert syncer.run_once().published == []


def test_a_rerender_replaces_the_published_audio(studio) -> None:
    _workspace, store, syncer = studio
    _request(store, "request-6", {"vocal_over_beat_db": 2.0})
    syncer.run_once()
    first = {e.path for e in store.list("songs/night-drive/audio/")}
    _request(store, "request-7", {"vocal_over_beat_db": 8.0})
    syncer.run_once()
    second = {e.path for e in store.list("songs/night-drive/audio/")}

    mixes = lambda paths: {p for p in paths if "/with_beat." in p}  # noqa: E731
    assert len(mixes(first)) == 1 and len(mixes(second)) == 1
    assert mixes(first) != mixes(second)


# ── store ───────────────────────────────────────────────────────────────────


def test_the_store_refuses_to_climb_out_of_itself(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "store")
    with pytest.raises(StoreError):
        store.put_json("../escape.json", {})


def test_listing_skips_half_written_files(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "store")
    store.put_json("feedback/s/a.json", {"ok": True})
    (tmp_path / "store" / "feedback" / "s" / "b.json.tmp").write_text("{")
    assert [e.path for e in store.list("feedback/")] == ["feedback/s/a.json"]
    assert json.loads(store.get_bytes("feedback/s/a.json")) == {"ok": True}
