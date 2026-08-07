"""The timer sound library: five generated built-ins, plus whatever the
household uploads.

**The built-ins are SYNTHESISED, not shipped.** A repo strangers are meant
to clone and run cannot carry sound files whose licence it cannot state,
and five short tones are a few dozen lines of `wave` and `math` against
zero dependencies. They are written on first boot and rewritten whenever
one goes missing, so a wiped volume heals itself rather than leaving a
timer with nothing to play.

Uploads live beside them on the same Docker volume as the SQLite DB, the
same arrangement `knowledge_service` and `recipe_image_service` use and
for the same reason: the project brief requires content to survive a
container rebuild, and the volume is the only thing that does.

A built-in cannot be deleted. It can be ignored, and the household can
upload anything it prefers, but a dropdown that can be emptied to nothing
is a timer that silently stops warning anyone.
"""

from __future__ import annotations

import contextlib
import math
import os
import struct
import uuid
import wave

SOUNDS_DIR = os.environ.get("SOUNDS_DIR", "/app/data/sounds")

# Deliberately narrow, same discipline as recipe_image_service: these are
# what a browser file picker realistically produces for audio, and an
# unrecognised type is refused rather than guessed at.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/aac": ".aac",
}

# Tuning, which turned out to be the whole job.
#
# The first version was harsh, and the author said so within a minute of
# hearing it. Two causes, and neither was "the wrong note".
#
# 1. THE ENVELOPE. Every tone was attack -> flat -> linear release. A sine
#    held at full amplitude for 1.8 seconds is a buzzer; nothing struck
#    behaves that way. A bell, a chime, a marimba bar all start loud and
#    decay exponentially from the first instant, and that decay is most of
#    what makes a sound read as pleasant rather than as an alert.
# 2. THE REGISTER. `Bell` was a 1318 Hz fundamental (E6) with a partial an
#    octave above it, so most of its energy sat where the ear is most
#    sensitive and least forgiving. Retuned an octave down.
#
# Partials are given as (ratio, amplitude) and roll off steeply. Ratios are
# consonant on purpose -- a real bell's inharmonic partials are what make
# real bells divisive, and this one has to be tolerable at 6am.
SAMPLE_RATE = 22050

# Bumped whenever the waveforms change, and part of the FILENAME.
#
# Load-bearing: ensure_builtin_files only writes a file that is missing,
# so without this an install that already has `builtin-bell.wav` would
# keep playing the old harsh one forever, and the fix would ship to nobody
# who had already run the app. Stale versions are pruned, and seeding
# re-points each row at the new path.
BUILTIN_VERSION = 2


def _decay(position: float, tau: float, attack: float = 0.012) -> float:
    """Fast attack, exponential decay. The attack is not zero because a
    waveform that starts at full amplitude clicks on every speaker ever
    made; 12ms is under the threshold where it reads as a soft onset."""
    if position < attack:
        return position / attack
    return math.exp(-(position - attack) / tau)


def _struck(
    frames: list[float],
    start: float,
    duration: float,
    fundamental: float,
    partials: tuple[tuple[float, float], ...] = ((1.0, 1.0),),
    peak: float = 0.25,
    tau: float | None = None,
) -> None:
    """One struck note: partials summed, exponential decay, faded to
    silence at the end so nothing is cut off mid-cycle."""
    tau = tau if tau is not None else duration / 3.0
    total = sum(amplitude for _ratio, amplitude in partials) or 1.0
    for index in range(int(duration * SAMPLE_RATE)):
        position = index / SAMPLE_RATE
        value = 0.0
        for ratio, amplitude in partials:
            value += amplitude * math.sin(2 * math.pi * fundamental * ratio * position)
        value *= _decay(position, tau) * peak / total
        # The last 8% fades out, so a decay that has not quite reached
        # silence does not end on a step.
        remaining = duration - position
        if remaining < duration * 0.08:
            value *= remaining / (duration * 0.08)
        slot = int((start + position) * SAMPLE_RATE)
        while len(frames) <= slot:
            frames.append(0.0)
        frames[slot] += value


def _soft_chime(frames: list[float]) -> None:
    """The default WARNING. Two notes a major third apart, rising, quiet.
    It has to be noticed without being obeyed -- there is still a minute
    left."""
    _struck(frames, 0.00, 1.10, 523.25, ((1.0, 1.0), (2.0, 0.12), (3.0, 0.04)), peak=0.20, tau=0.45)
    _struck(frames, 0.28, 1.30, 659.25, ((1.0, 1.0), (2.0, 0.12), (3.0, 0.04)), peak=0.20, tau=0.55)


def _warm_bell(frames: list[float]) -> None:
    """The default FINISH. An octave below the old one, decaying properly,
    with a fifth and a double octave underneath for body."""
    _struck(frames, 0.0, 2.4, 440.0, ((1.0, 1.0), (2.0, 0.22), (3.0, 0.07), (4.0, 0.03)), peak=0.28, tau=0.85)


def _marimba(frames: list[float]) -> None:
    """Wooden and short. A marimba bar's strong fourth harmonic is what
    makes it sound like wood rather than like a sine."""
    for offset, note in ((0.0, 587.33), (0.16, 880.0)):
        _struck(frames, offset, 0.7, note, ((1.0, 1.0), (4.0, 0.22)), peak=0.26, tau=0.16)


def _ping(frames: list[float]) -> None:
    """One quiet note. For a household that wants to be told, not told
    off."""
    _struck(frames, 0.0, 0.9, 659.25, ((1.0, 1.0), (2.0, 0.06)), peak=0.16, tau=0.28)


def _alarm(frames: list[float]) -> None:
    """Deliberately insistent, and kept because some kitchens need it --
    but it is now something a household CHOOSES rather than the default it
    became by being first alphabetically."""
    for index in range(6):
        _struck(
            frames, index * 0.20, 0.19, 900.0 if index % 2 else 720.0, ((1.0, 1.0), (2.0, 0.18)), peak=0.30, tau=0.06
        )


# (slug, display name, builder, default_for). `default_for` is what a
# fresh install picks, and it is stated HERE rather than inferred from
# list position -- the library sorts alphabetically, which quietly made
# `Alarm (urgent)` the default warning and a 1318Hz bell the default
# finish. Position is not a decision.
BUILTIN_SOUNDS: list[tuple[str, str, object, str | None]] = [
    ("soft-chime", "Soft chime", _soft_chime, "warning"),
    ("warm-bell", "Warm bell", _warm_bell, "finish"),
    ("marimba", "Marimba", _marimba, None),
    ("ping", "Quiet ping", _ping, None),
    ("alarm", "Alarm (urgent)", _alarm, None),
]


def _write_wav(path: str, builder) -> None:
    frames: list[float] = []
    builder(frames)
    peak = max((abs(f) for f in frames), default=1.0) or 1.0
    scale = min(1.0, 0.9 / peak)
    payload = b"".join(struct.pack("<h", int(max(-1.0, min(1.0, f * scale)) * 32767)) for f in frames)

    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    with wave.open(tmp, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(payload)
    os.replace(tmp, path)


def builtin_path(slug: str) -> str:
    return os.path.join(SOUNDS_DIR, f"builtin-{slug}-v{BUILTIN_VERSION}.wav")


def ensure_builtin_files() -> list[str]:
    """Writes any built-in whose file is missing. Returns the slugs it had
    to (re)create -- empty on a healthy boot, which is the normal case."""
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    created = []
    current = set()
    for slug, _name, builder, _default_for in BUILTIN_SOUNDS:
        path = builtin_path(slug)
        current.add(os.path.basename(path))
        if not os.path.exists(path):
            _write_wav(path, builder)
            created.append(slug)

    # Sweep superseded versions, so bumping BUILTIN_VERSION does not leave
    # the old harsh tones on the volume forever. Only files this function
    # writes are ever touched -- an upload has a UUID name and is never
    # matched here.
    for name in os.listdir(SOUNDS_DIR):
        if name.startswith("builtin-") and name not in current:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(SOUNDS_DIR, name))
    return created


def save_upload(filename: str, content_type: str | None, raw_bytes: bytes, max_bytes: int = 2_000_000) -> str:
    """Writes an uploaded sound and returns its storage path.

    The size cap is not arbitrary caution: this is a notification tone
    that has to start playing the instant a timer ends, and a file large
    enough to buffer is a file that rings late."""
    extension = ALLOWED_CONTENT_TYPES.get((content_type or "").lower().split(";")[0].strip())
    if extension is None:
        extension = os.path.splitext(filename or "")[1].lower()
        if extension not in set(ALLOWED_CONTENT_TYPES.values()):
            raise ValueError(f"Unsupported audio type: {content_type or filename!r}")
    if len(raw_bytes) > max_bytes:
        raise ValueError(f"Sound is {len(raw_bytes) // 1000}KB; the limit is {max_bytes // 1000}KB")
    if not raw_bytes:
        raise ValueError("That file is empty")

    os.makedirs(SOUNDS_DIR, exist_ok=True)
    path = os.path.join(SOUNDS_DIR, f"{uuid.uuid4().hex}{extension}")
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as handle:
        handle.write(raw_bytes)
    os.replace(tmp, path)
    return path


def delete_file(path: str) -> None:
    with contextlib.suppress(OSError):
        os.remove(path)


_CONTENT_TYPE_BY_EXTENSION = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
}


def guess_content_type(path: str) -> str:
    return _CONTENT_TYPE_BY_EXTENSION.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
