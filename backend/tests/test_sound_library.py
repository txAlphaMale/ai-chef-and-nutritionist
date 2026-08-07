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

    for slug, _name, _builder, _role in sound_service.BUILTIN_SOUNDS:
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
        slug: Path(sound_service.builtin_path(slug)).read_bytes() for slug, _n, _b, _r in sound_service.BUILTIN_SOUNDS
    }
    assert len(set(payloads.values())) == len(sound_service.BUILTIN_SOUNDS)


def test_generation_is_idempotent_and_heals_a_wiped_volume():
    assert sorted(sound_service.ensure_builtin_files()) == sorted(s for s, _n, _b, _r in sound_service.BUILTIN_SOUNDS)
    assert sound_service.ensure_builtin_files() == []

    import os

    os.remove(sound_service.builtin_path("warm-bell"))
    assert sound_service.ensure_builtin_files() == ["warm-bell"]


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

    bell = db_session.query(SoundFile).filter_by(slug="warm-bell").first()
    bell.name = "The Good Bell"
    db_session.commit()

    seed_builtin_sounds(db_session)
    assert db_session.query(SoundFile).filter_by(slug="warm-bell").first().name == "The Good Bell"


def test_a_reseed_repoints_a_row_whose_file_moved(db_session):
    """The row lives in SQLite and the audio on the data volume, so a
    restore can bring back one without the other."""
    seed_builtin_sounds(db_session)
    row = db_session.query(SoundFile).filter_by(slug="soft-chime").first()
    row.storage_path = "/gone/soft-chime.wav"
    db_session.commit()

    seed_builtin_sounds(db_session)
    row = db_session.query(SoundFile).filter_by(slug="soft-chime").first()
    assert row.storage_path == sound_service.builtin_path("soft-chime")


def test_the_wav_header_is_what_a_browser_will_be_handed():
    sound_service.ensure_builtin_files()
    raw = Path(sound_service.builtin_path("alarm")).read_bytes()
    assert raw[:4] == b"RIFF" and raw[8:12] == b"WAVE"
    with wave.open(io.BytesIO(raw), "rb") as handle:
        assert handle.getnframes() > 0
    assert sound_service.guess_content_type("x.wav") == "audio/wav"
    assert sound_service.guess_content_type("x.mp3") == "audio/mpeg"


def test_bumping_the_version_replaces_the_old_audio_rather_than_keeping_it():
    """The whole reason the filename carries a version.

    `ensure_builtin_files` only writes a file that is MISSING, so without
    the version in the name an install that already had the first, harsh
    set would keep playing it forever and the retune would ship to nobody
    who had already run the app."""
    import os

    sound_service.ensure_builtin_files()
    stale = os.path.join(sound_service.SOUNDS_DIR, "builtin-warm-bell-v1.wav")
    with open(stale, "wb") as handle:
        handle.write(b"old harsh bell")

    sound_service.ensure_builtin_files()

    assert not os.path.exists(stale), "a superseded built-in was left on the volume"
    assert os.path.exists(sound_service.builtin_path("warm-bell"))


def test_the_sweep_never_touches_an_uploaded_sound():
    """Uploads have UUID names and are nobody's business but the
    household's."""
    import os

    sound_service.ensure_builtin_files()
    upload = sound_service.save_upload("mine.wav", "audio/wav", b"RIFF0000WAVEfmt mine")

    sound_service.ensure_builtin_files()
    assert os.path.exists(upload)


def test_every_builtin_states_what_it_is_for_or_states_nothing():
    """`default_for` is how a fresh install picks its pair. Exactly one
    warning and exactly one finish -- two candidates for a slot is a
    coin-flip, and none is a timer that ends in silence."""
    roles = [role for _slug, _name, _builder, role in sound_service.BUILTIN_SOUNDS]
    assert roles.count("warning") == 1
    assert roles.count("finish") == 1
    assert all(role in (None, "warning", "finish") for role in roles)


def test_a_retired_builtin_does_not_linger_as_a_dead_row(db_session):
    """v1's `bell` became v2's `warm-bell`. Without retirement the old
    row survives, its file has just been swept, and the library shows a
    dead entry that any existing timer preference may still point at."""
    from app.models import SoundFile

    db_session.add(SoundFile(name="Bell", storage_path="/old/builtin-bell.wav", slug="bell", is_builtin=True))
    db_session.commit()

    seed_builtin_sounds(db_session)

    assert db_session.query(SoundFile).filter_by(slug="bell").first() is None
    assert db_session.query(SoundFile).filter_by(slug="warm-bell").first() is not None


def test_retirement_never_touches_an_upload(db_session):
    """An upload has no slug, and `is_builtin` is the discriminator --
    not the absence of a name in the current set."""
    from app.models import SoundFile

    db_session.add(SoundFile(name="My gong", storage_path="/data/abc.wav", slug=None, is_builtin=False))
    db_session.commit()

    seed_builtin_sounds(db_session)

    assert db_session.query(SoundFile).filter_by(name="My gong").first() is not None
