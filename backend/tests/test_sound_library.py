"""The cooking-timer sound library.

The built-ins are SYNTHESISED rather than shipped, which is the decision
most of this file is really testing. A repo strangers clone cannot carry
audio whose licence it cannot state, and five tones are a few dozen lines
of `wave` and `math` against zero dependencies -- so the interesting
questions are whether they are real playable audio, whether they heal
themselves when the volume is wiped, and whether the library can ever be
emptied to nothing (it must not: every timer picks its warning and finish
sound from this list).
"""

import io
import wave
from pathlib import Path

import pytest

from app.models import SoundFile
from app.seed import seed_builtin_sounds
from app.services import sound_service


@pytest.fixture(autouse=True)
def _sounds_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(sound_service, "SOUNDS_DIR", str(tmp_path / "sounds"))


def test_every_builtin_is_real_playable_audio():
    """Not merely "a file exists" -- opened as a WAV, with the format and
    a non-trivial length asserted, because a zero-length or malformed
    file is exactly what a silent timer looks like."""
    sound_service.ensure_builtin_files()

    for slug, _name, _builder in sound_service.BUILTIN_SOUNDS:
        with wave.open(sound_service.builtin_path(slug), "rb") as handle:
            assert handle.getnchannels() == 1, slug
            assert handle.getsampwidth() == 2, slug
            assert handle.getframerate() == sound_service.SAMPLE_RATE, slug
            duration = handle.getnframes() / handle.getframerate()
            assert 0.3 < duration < 3.0, (slug, duration)


def test_the_builtins_are_audibly_different_from_each_other():
    """A warning tone and a finish tone that sound the same are one tone.
    Compared by bytes, which is crude and sufficient: identical builders
    would be caught, and that is the failure worth catching."""
    sound_service.ensure_builtin_files()
    payloads = {
        slug: Path(sound_service.builtin_path(slug)).read_bytes() for slug, _n, _b in sound_service.BUILTIN_SOUNDS
    }
    assert len(set(payloads.values())) == len(sound_service.BUILTIN_SOUNDS)


def test_generation_is_idempotent_and_heals_a_wiped_volume():
    assert sorted(sound_service.ensure_builtin_files()) == sorted(s for s, _n, _b in sound_service.BUILTIN_SOUNDS)
    assert sound_service.ensure_builtin_files() == []

    import os

    os.remove(sound_service.builtin_path("bell"))
    assert sound_service.ensure_builtin_files() == ["bell"]


def test_an_unsupported_upload_is_refused_rather_than_guessed_at():
    with pytest.raises(ValueError, match="Unsupported audio type"):
        sound_service.save_upload("payload.exe", "application/x-msdownload", b"MZ")


def test_an_oversized_upload_is_refused_because_a_slow_sound_rings_late():
    with pytest.raises(ValueError, match="the limit is"):
        sound_service.save_upload("huge.wav", "audio/wav", b"x" * 3_000_000, max_bytes=2_000_000)


def test_an_empty_upload_is_refused():
    with pytest.raises(ValueError, match="empty"):
        sound_service.save_upload("nothing.wav", "audio/wav", b"")


def test_an_extension_carries_a_file_whose_content_type_the_browser_got_wrong():
    """Browsers report `application/octet-stream` for audio often enough
    that refusing on content type alone would reject real files."""
    path = sound_service.save_upload("bell.mp3", "application/octet-stream", b"ID3data")
    assert path.endswith(".mp3")


def test_seeding_is_idempotent_and_does_not_overwrite_a_renamed_builtin(db_session):
    assert len(seed_builtin_sounds(db_session)) == len(sound_service.BUILTIN_SOUNDS)
    assert seed_builtin_sounds(db_session) == []

    bell = db_session.query(SoundFile).filter_by(slug="bell").first()
    bell.name = "The Good Bell"
    db_session.commit()

    seed_builtin_sounds(db_session)
    assert db_session.query(SoundFile).filter_by(slug="bell").first().name == "The Good Bell"


def test_a_reseed_repoints_a_row_whose_file_moved(db_session):
    """The row lives in SQLite and the audio on the data volume, so a
    restore can bring back one without the other."""
    seed_builtin_sounds(db_session)
    row = db_session.query(SoundFile).filter_by(slug="chime").first()
    row.storage_path = "/gone/chime.wav"
    db_session.commit()

    seed_builtin_sounds(db_session)
    assert db_session.query(SoundFile).filter_by(slug="chime").first().storage_path == sound_service.builtin_path(
        "chime"
    )


def test_the_wav_header_is_what_a_browser_will_be_handed():
    sound_service.ensure_builtin_files()
    raw = Path(sound_service.builtin_path("alarm")).read_bytes()
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    with wave.open(io.BytesIO(raw), "rb") as handle:
        assert handle.getnframes() > 0
    assert sound_service.guess_content_type("x.wav") == "audio/wav"
    assert sound_service.guess_content_type("x.mp3") == "audio/mpeg"
