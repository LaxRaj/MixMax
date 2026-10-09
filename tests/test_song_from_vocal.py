"""G4 — vocal in, finished song out, with the fake generator on synthetic audio."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from producer.benchmark import measure
from producer.blindtest import publish_listening_test
from producer.cli import cli
from producer.generate.fake import SOURCE_ENV, FakeGenerator
from producer.generate.gate import FAIL, INCOMPLETE, PASS, RETRY, judge_vocal, phase_a_gate
from producer.generate.pipeline import (
    VERSION_PIPELINE,
    VERSION_RAW,
    IntakeBlocked,
    NoSurvivors,
    song_from_vocal,
)
from producer.song import (
    BEAT,
    GROUPS,
    MASTER,
    VOCAL_MIXED,
    WITH_BEAT,
    RebuildError,
    SettingsError,
    apply_text,
    group_applies,
    load_recorded,
    load_text,
    rebuild,
    save_settings,
    validate_text,
)
from tests.synth import backing, melody_vocal, write


def _ledger_lines(song_dir: Path) -> int:
    return len((song_dir / "generated" / "ledger.jsonl").read_text().splitlines())


@pytest.fixture(scope="module")
def material(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("sfv")
    beats = root / "beats"
    write(beats / "a_matched.wav", backing(100.0))
    write(beats / "b_fast.wav", backing(106.0))
    write(beats / "c_far.wav", backing(125.0))
    wrong = root / "wrong"
    write(wrong / "far.wav", backing(125.0))
    # A backing no first run was offered, so a regeneration is visibly a new one.
    write(root / "other" / "other.wav", backing(100.0, seed=9) * 0.8)
    return {
        "root": root,
        "vocal": write(root / "night song.wav", melody_vocal(100.0)),
        "beats": beats,
        "wrong": wrong,
        "other": root / "other",
    }


@pytest.fixture(scope="module")
def song(material: dict[str, Path]) -> dict:
    """One full run, shared by the tests that only read it."""
    workspace = material["root"] / "ws"
    summary = song_from_vocal(
        material["vocal"], "warm lofi", workspace,
        generator=FakeGenerator(material["beats"]), n_candidates=3, seed=0,
        blind_dir=material["root"] / "blind",
    )
    return {"summary": summary, "dir": workspace / summary["slug"], "workspace": workspace}


# ── the run ─────────────────────────────────────────────────────────────────


def test_generate_is_the_first_group() -> None:
    assert GROUPS == ("generate", "vocal", "balance", "master", "arrangement")


def test_end_to_end_writes_every_artifact(song: dict) -> None:
    song_dir, summary = song["dir"], song["summary"]

    assert summary["slug"] == "night-song"
    for name in ("original.wav", "spec.json", VOCAL_MIXED, BEAT, WITH_BEAT, MASTER,
                 "summary.json", "settings.json", "generated/generation.json",
                 "generated/ledger.jsonl"):
        assert (song_dir / name).exists(), name
    assert _ledger_lines(song_dir) == 3
    assert json.loads((song_dir / "summary.json").read_text())["chosen"] == summary["chosen"]

    assert summary["spec"]["tempo_bpm"] == pytest.approx(100.0, abs=1.0)
    assert summary["spec"]["key"] == "A minor"
    assert summary["cost_usd"] == 0.0
    assert load_text(song_dir) == {"backend": "fake", "style": "warm lofi"}


def test_candidates_are_fitted_or_dropped_with_reasons(song: dict) -> None:
    song_dir = song["dir"]
    rows = {Path(r["raw"]).name: r for r in song["summary"]["candidates"]}
    by_source = {
        Path(result["meta"]["source"]).stem: rows[Path(result["path"]).name]
        for result in song["summary"]["generation"]["results"]
    }

    matched, fast, far = by_source["a_matched"], by_source["b_fast"], by_source["c_far"]
    assert matched["passed_fit"] and matched["fit"]["stretch"] == 1.0
    assert fast["passed_fit"] and fast["fit"]["stretch"] == pytest.approx(100 / 106, abs=0.01)
    assert not far["passed_fit"] and far["reasons"][0].startswith("Tempo")

    assert song["summary"]["survivors"] == 2
    assert song["summary"]["rejected"] == [{"index": far["index"], "reasons": far["reasons"]}]

    for row in (matched, fast):
        cand = song_dir / f"cand_{row['index']}"
        for name in (BEAT, WITH_BEAT, MASTER, "fit.json", "qa.json", "vendor_raw_with_vocal.wav"):
            assert (cand / name).exists(), name
        assert row["qa"]["pass"], row["qa"]["flags"]
    rejected_dir = song_dir / f"cand_{far['index']}"
    assert (rejected_dir / "fit.json").exists()
    assert not (rejected_dir / MASTER).exists() and not (rejected_dir / BEAT).exists()

    chosen = song["summary"]["chosen"]
    assert chosen in (matched["index"], fast["index"])
    assert (song_dir / MASTER).read_bytes() == (song_dir / f"cand_{chosen}" / MASTER).read_bytes()


def test_the_master_lands_on_the_delivery_target(song: dict) -> None:
    assert measure(song["dir"] / MASTER)["lufs"] == pytest.approx(-16.0, abs=0.6)


def test_blind_test_compares_raw_output_with_the_pipeline(song: dict, material: dict) -> None:
    blind = song["summary"]["blind_test"]
    share, key_path = Path(blind["share"]), Path(blind["key"])

    assert share == material["root"] / "blind" / "night-song"
    assert key_path.exists() and share not in key_path.parents      # the key is not shared
    key = json.loads(key_path.read_text())
    assert set(key["mapping"].values()) == {VERSION_RAW, VERSION_PIPELINE}

    listen = sorted((share / "listen").glob("*.wav"))
    assert [p.stem for p in listen] == ["A", "B"]
    loudness = [measure(p)["lufs"] for p in listen]
    assert abs(loudness[0] - loudness[1]) < 0.5                     # judged on sound, not level

    instructions = (share / "INSTRUCTIONS.md").read_text()
    assert "Would you release this?" in instructions
    assert "Which sounds more finished?" in instructions
    # Nothing a listener receives names a version.
    shared = instructions + (share / "SCORESHEET.csv").read_text()
    assert VERSION_RAW not in shared and VERSION_PIPELINE not in shared


def test_zero_survivors_fails_loudly_and_ships_nothing(material: dict, tmp_path: Path) -> None:
    with pytest.raises(NoSurvivors) as excinfo:
        song_from_vocal(material["vocal"], "trap", tmp_path / "ws",
                        generator=FakeGenerator(material["wrong"]), n_candidates=2,
                        blind_dir=tmp_path / "blind")

    message = str(excinfo.value)
    assert "None of the 2 candidate(s) fits" in message and "Tempo" in message

    song_dir = tmp_path / "ws" / "night-song"
    summary = json.loads((song_dir / "summary.json").read_text())
    assert summary["chosen"] is None and summary["survivors"] == 0
    assert len(summary["rejected"]) == 2 and all(r["reasons"] for r in summary["rejected"])
    for name in (BEAT, WITH_BEAT, MASTER):
        assert not (song_dir / name).exists()
    assert not (tmp_path / "blind").exists()


def test_a_blocked_vocal_spends_nothing(clipped_wav: Path, material: dict, tmp_path: Path) -> None:
    generator = FakeGenerator(material["beats"])
    with pytest.raises(IntakeBlocked):
        song_from_vocal(clipped_wav, "lofi", tmp_path / "ws", generator=generator)
    assert generator.calls == 0
    assert not (tmp_path / "ws" / "clipped").exists()


def test_cli_runs_end_to_end_and_exits_nonzero_with_no_survivors(
    material: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SOURCE_ENV, str(material["beats"] / "a_matched.wav"))
    args = ["song-from-vocal", "--vocal", str(material["vocal"]), "--style", "lofi",
            "--workspace", str(tmp_path / "ws"), "--n", "1", "--blind-dir", str(tmp_path / "blind")]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert "candidate 1: fits" in result.output and "<- chosen" in result.output
    assert "not generated" in result.output
    assert (tmp_path / "ws" / "night-song" / "summary.json").stat().st_size > 0
    assert (tmp_path / "blind" / "night-song.key.json").exists()

    monkeypatch.setenv(SOURCE_ENV, str(material["wrong"]))
    result = CliRunner().invoke(cli, [*args[:6], str(tmp_path / "ws2"), "--n", "1", "--no-blind"])
    assert result.exit_code != 0
    assert "Tempo" in result.output


# ── regenerating through rebuild ────────────────────────────────────────────


@pytest.fixture()
def copy_of_song(song: dict, tmp_path: Path) -> Path:
    return Path(shutil.copytree(song["dir"], tmp_path / "night-song"))


def test_a_style_change_regenerates_and_rerenders_downstream(
    copy_of_song: Path, material: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SOURCE_ENV, str(material["other"]))
    before = {name: (copy_of_song / name).read_bytes() for name in (BEAT, WITH_BEAT, MASTER)}
    assert _ledger_lines(copy_of_song) == 3

    rendered = apply_text(copy_of_song, {"style": "darker, slower"})

    assert _ledger_lines(copy_of_song) == 6          # the generator was called again
    assert rendered[0] == BEAT and {WITH_BEAT, MASTER} <= set(rendered)
    for name in (BEAT, WITH_BEAT, MASTER):
        assert (copy_of_song / name).read_bytes() != before[name], name
    assert load_text(copy_of_song)["style"] == "darker, slower"
    # Renders made over the old backing do not outlive it.
    assert not list(copy_of_song.glob("cand_*/MASTER.wav"))
    summary = json.loads((copy_of_song / "summary.json").read_text())
    assert summary["regenerated"] is True and "blind_test" not in summary
    assert not (copy_of_song / ".render").exists()


def test_a_downstream_change_does_not_call_the_generator(copy_of_song: Path) -> None:
    rebuild(copy_of_song, "balance", values={"duck_db": 6.0})
    rebuild(copy_of_song, "master", values={"target_lufs": -14.0})
    assert _ledger_lines(copy_of_song) == 3


def test_a_failed_regeneration_leaves_the_song_but_keeps_the_ledger(
    copy_of_song: Path, material: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SOURCE_ENV, str(material["wrong"]))
    before = {name: (copy_of_song / name).read_bytes() for name in (BEAT, WITH_BEAT, MASTER)}

    with pytest.raises(RebuildError, match="fits this vocal"):
        apply_text(copy_of_song, {"style": "something else"})

    for name, content in before.items():
        assert (copy_of_song / name).read_bytes() == content
    assert load_text(copy_of_song)["style"] == "warm lofi"
    assert _ledger_lines(copy_of_song) == 6          # the attempt is still on the books


def test_an_uploaded_beat_is_never_regenerated(tmp_path: Path, material: dict) -> None:
    song_dir = tmp_path / "mine"
    song_dir.mkdir()
    shutil.copyfile(material["vocal"], song_dir / "original.wav")
    shutil.copyfile(material["beats"] / "a_matched.wav", song_dir / BEAT)
    mine = (song_dir / BEAT).read_bytes()

    assert group_applies(song_dir, "generate")[0] is False
    with pytest.raises(SettingsError, match="not generated here"):
        apply_text(song_dir, {"style": "anything"})
    with pytest.raises(RebuildError, match="not generated here"):
        rebuild(song_dir, "generate")
    assert (song_dir / BEAT).read_bytes() == mine


# ── text settings ───────────────────────────────────────────────────────────


def test_text_settings_are_validated() -> None:
    assert validate_text({"style": "  dusty soul  "}) == {"style": "dusty soul"}
    for bad in ({}, {"style": ""}, {"style": 7}, {"style": "x" * 401}, {"vibe": "nice"},
                {"backend": "no-such-backend"}):
        with pytest.raises(SettingsError):
            validate_text(bad)


def test_numeric_settings_do_not_erase_text_ones(copy_of_song: Path) -> None:
    save_settings(copy_of_song, {**load_recorded(copy_of_song), "duck_db": 4.0})
    assert load_text(copy_of_song)["style"] == "warm lofi"
    assert load_recorded(copy_of_song)["duck_db"] == 4.0


# ── publishing to the listening page ────────────────────────────────────────


def test_publish_ships_labels_only(song: dict, tmp_path: Path) -> None:
    share = Path(song["summary"]["blind_test"]["share"])
    public = tmp_path / "public"
    (public / "tests").mkdir(parents=True)
    (public / "tests" / "index.json").write_text(json.dumps({"tests": [{"slug": "older"}]}))

    def copy(source: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)

    for _ in range(2):   # publishing twice must not list the test twice
        published = publish_listening_test(share, public, "song-night-song", "Would you release this?",
                                           "Two finishes of the same song.", encode=copy)

    manifest_text = (public / "tests" / "song-night-song.json").read_text()
    manifest = json.loads(manifest_text)
    assert manifest["labels"] == ["A", "B"] and published["length_s"] > 20
    assert (public / "audio" / "song-night-song" / "A.m4a").exists()
    assert VERSION_RAW not in manifest_text and VERSION_PIPELINE not in manifest_text
    index = json.loads((public / "tests" / "index.json").read_text())["tests"]
    assert [t["slug"] for t in index] == ["older", "song-night-song"]

    with pytest.raises(ValueError, match="un-blind"):
        publish_listening_test(share, public, "pipeline-vs-raw", "t", "b", encode=copy)


# ── the gate ────────────────────────────────────────────────────────────────


def _tally(answers: list[tuple[float, int]]) -> dict:
    """`answers`: per listener, (release score for the pipeline, its rank)."""
    rows = []
    for i, (score, rank) in enumerate(answers):
        rows.append({"listener": f"l{i}", "source": VERSION_PIPELINE, "score": score, "rank": rank})
        rows.append({"listener": f"l{i}", "source": VERSION_RAW, "score": 2.0, "rank": 3 - rank})
    return {"rows": rows}


GOOD = [(4, 1), (5, 1), (4, 2)]        # median 4, preferred 2-1
LOW = [(3, 1), (3, 1), (5, 1)]         # preferred, but median 3
DISLIKED = [(5, 2), (4, 2), (4, 1)]    # scored well, but raw preferred


def test_a_vocal_needs_the_score_and_the_preference_and_the_listeners() -> None:
    assert judge_vocal("a", _tally(GOOD))["passed"]
    assert not judge_vocal("a", _tally(LOW))["passed"]
    assert not judge_vocal("a", _tally(DISLIKED))["passed"]
    thin = judge_vocal("a", _tally(GOOD[:2]))
    assert not thin["passed"] and not thin["enough_listeners"] and thin["problems"]


def test_gate_decisions_follow_the_written_thresholds() -> None:
    def gate(n_good: int, total: int = 5) -> str:
        vocals = [judge_vocal(f"v{i}", _tally(GOOD if i < n_good else LOW)) for i in range(total)]
        return phase_a_gate(vocals)["decision"]

    assert [gate(n) for n in range(6)] == [FAIL, FAIL, RETRY, PASS, PASS, PASS]
    assert gate(4, total=4) == INCOMPLETE              # four vocals is not five

    short = [judge_vocal(f"v{i}", _tally(GOOD)) for i in range(4)]
    short.append(judge_vocal("v4", _tally(GOOD[:2])))
    assert phase_a_gate(short)["decision"] == INCOMPLETE   # too few listeners on one


def test_gate_cli_reads_keys_and_responses(tmp_path: Path) -> None:
    args = ["gate"]
    for i in range(5):
        key = tmp_path / f"v{i}.key.json"
        key.write_text(json.dumps({"target_lufs": -16.0, "mapping": {"A": VERSION_PIPELINE,
                                                                     "B": VERSION_RAW}}))
        responses = tmp_path / f"responses{i}.csv"
        lines = ["listener,label,release_ready_1_5,rank,notes"]
        for name in ("ana", "ben", "cy"):
            lines += [f"{name},A,{5 if i < 3 else 2},1,", f"{name},B,2,2,"]
        responses.write_text("\n".join(lines) + "\n")
        args += ["--test", str(key), str(responses)]

    result = CliRunner().invoke(cli, [*args, "--out", str(tmp_path / "gate.md")])
    assert result.exit_code == 0, result.output
    assert "**PASS**" in result.output and "3 of 5 vocals passed" in result.output
    assert "| v4 | 3 | 2 |" in (tmp_path / "gate.md").read_text()
