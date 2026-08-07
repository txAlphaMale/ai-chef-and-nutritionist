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

# A warning tone has to be audible across a kitchen without being the
# same thing as "done", so the set is chosen for CONTRAST -- pitch,
# rhythm and decay all differ -- rather than for variety's sake.
SAMPLE_RATE = 22050
_AMPLITUDE = 0.35


def _envelope(position: float, length: float, attack: float = 0.01, release: float = 0.25) -> float:
    """Fade in and out, because a square-edged tone clicks on every
    speaker ever made."""
    if position < attack:
        return position / attack
    if position > length - release:
        return max(0.0, (length - position) / release)
    return 1.0


def _tone(frames: list[float], start: float, length: float, frequency: float, harmonic: float = 0.0) -> None:
    for index in range(int(length * SAMPLE_RATE)):
        position = index / SAMPLE_RATE
        value = math.sin(2 * math.pi * frequency * position)
        if harmonic:
            value += harmonic * math.sin(4 * math.pi * frequency * position)
        value *= _envelope(position, length) * _AMPLITUDE / (1 + harmonic)
        slot = int((start + position) * SAMPLE_RATE)
        while len(frames) <= slot:
            frames.append(0.0)
        frames[slot] += value


def _sweep(frames: list[float], start: float, length: float, low: float, high: float) -> None:
    phase = 0.0
    for index in range(int(length * SAMPLE_RATE)):
        position = index / SAMPLE_RATE
        frequency = low + (high - low) * (position / length)
        phase += 2 * math.pi * frequency / SAMPLE_RATE
        value = math.sin(phase) * _envelope(position, length, release=length * 0.5) * _AMPLITUDE
        slot = int((start + position) * SAMPLE_RATE)
        while len(frames) <= slot:
            frames.append(0.0)
        frames[slot] += value


def _chime(frames: list[float]) -> None:
    for beat in (0.0, 0.55, 1.10):
        _tone(frames, beat, 0.45, 880.0)


def _bell(frames: list[float]) -> None:
    _tone(frames, 0.0, 1.8, 1318.5, harmonic=0.4)


def _soft_ping(frames: list[float]) -> None:
    _tone(frames, 0.0, 0.7, 659.3, harmonic=0.15)


def _alarm(frames: list[float]) -> None:
    for index in range(6):
        _tone(frames, index * 0.22, 0.18, 1000.0 if index % 2 else 800.0)


def _rising(frames: list[float]) -> None:
    _sweep(frames, 0.0, 0.9, 400.0, 1200.0)


# (slug, display name, builder). The slug is the filename stem and the
# stable identity across rebuilds -- names are for humans and may change.
BUILTIN_SOUNDS: list[tuple[str, str, object]] = [
    ("chime", "Chime (three beeps)", _chime),
    ("bell", "Bell", _bell),
    ("soft-ping", "Soft ping", _soft_ping),
    ("alarm", "Alarm (urgent)", _alarm),
    ("rising", "Rising sweep", _rising),
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
    return os.path.join(SOUNDS_DIR, f"builtin-{slug}.wav")


def ensure_builtin_files() -> list[str]:
    """Writes any built-in whose file is missing. Returns the slugs it had
    to (re)create -- empty on a healthy boot, which is the normal case."""
    os.makedirs(SOUNDS_DIR, exist_ok=True)
    created = []
    for slug, _name, builder in BUILTIN_SOUNDS:
        path = builtin_path(slug)
        if not os.path.exists(path):
            _write_wav(path, builder)
            created.append(slug)
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
