"""Audio and video — identified precisely, and honest about what is unread.

A repository holding a 2GB screen recording is a fact worth knowing, and so is the
fact that **nobody has heard it**. Those are two different statements and the
platform must make both: the container is identified and its metadata modelled,
while the *content* — speech, slides on screen, what was said — is marked
`UNREADABLE` with the reason.

That split is the whole design. Silently returning an empty document for a video
would render it as a file that legitimately contains nothing, and a reviewer would
never learn that forty minutes of design discussion sits unindexed. Ring 1 supplies
transcription through the same extractor protocol; the kernel states the gap.

What is done here, with the stdlib alone:

* **identification** from container magic — MP4/MOV `ftyp`, Matroska/WebM EBML,
  OGG, RIFF/WAV, FLAC, MP3 frame sync or ID3;
* **metadata** where the container puts it in a fixed place — WAV's `fmt ` chunk
  and MP4's `mvhd` atom both yield a real duration, sample rate and channel count
  without decoding a single frame.

Duration matters more than it looks: it is the unit in which the *cost* of the
missing transcript is expressed. "45 minutes unheard" is actionable; "one video
file" is not.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .document import Block, BlockKind, unreadable
from .locator import of_file

#: Container magic. Some sit at a fixed offset rather than at the start: an MP4's
#: `ftyp` box begins at byte 4, after its length.
MEDIA_MAGIC: tuple[tuple[int, bytes, str, str], ...] = (
    (4, b"ftyp", "mp4", "video"),
    (0, b"\x1aE\xdf\xa3", "matroska", "video"),
    (0, b"OggS", "ogg", "audio"),
    (0, b"RIFF", "riff", ""),           # WAV or AVI; the form decides
    (0, b"fLaC", "flac", "audio"),
    (0, b"ID3", "mp3", "audio"),
    (0, b"\xff\xfb", "mp3", "audio"),
    (0, b"\xff\xf3", "mp3", "audio"),
    (0, b"FORM", "aiff", "audio"),
)

#: `ftyp` brands that name something more specific than "an MP4 container".
MP4_BRANDS: Mapping[bytes, str] = {
    b"qt  ": "mov", b"M4A ": "m4a", b"M4V ": "m4v",
    b"isom": "mp4", b"mp42": "mp4", b"avc1": "mp4", b"iso2": "mp4",
}


@dataclass(frozen=True, slots=True)
class Media:
    """What a container says about itself, without decoding it."""

    container: str
    family: str                      # audio · video
    seconds: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    codec: str = ""
    confidence: float = 0.0

    @property
    def duration(self) -> str:
        """`45m 12s` — the unit the missing transcript's cost is expressed in."""
        if self.seconds <= 0:
            return "unknown"
        total = int(self.seconds)
        hours, rest = divmod(total, 3600)
        minutes, seconds = divmod(rest, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {seconds:02d}s"
        return f"{seconds}s"

    def to_dict(self) -> dict[str, Any]:
        return {
            "container": self.container, "family": self.family,
            "seconds": round(self.seconds, 2), "duration": self.duration,
            "sample_rate": self.sample_rate, "channels": self.channels,
            "codec": self.codec, "confidence": self.confidence,
        }

    def __str__(self) -> str:
        return f"{self.container} {self.family}, {self.duration}"


def sniff(payload: bytes) -> Media | None:
    """Which media container this is, or None. Reads only the header."""
    head = payload[:4096]

    for offset, marker, container, family in MEDIA_MAGIC:
        if head[offset:offset + len(marker)] != marker:
            continue

        if container == "mp4":
            brand = head[8:12]
            resolved = MP4_BRANDS.get(brand, "mp4")
            return Media(
                container=resolved,
                family="audio" if resolved == "m4a" else "video",
                seconds=_mp4_duration(payload), confidence=0.97,
            )

        if container == "riff":
            form = head[8:12]
            if form == b"WAVE":
                return _wav(payload)
            if form == b"AVI ":
                return Media(container="avi", family="video", confidence=0.95)
            return Media(container="riff", family="", confidence=0.7)

        return Media(container=container, family=family, confidence=0.95)

    return None


def _wav(payload: bytes) -> Media:
    """WAV's `fmt ` chunk gives rate, channels and — with `data` — a duration.

    Chunks are walked rather than assumed at a fixed offset: a WAV may carry
    `LIST` or `fact` chunks before `fmt `, and reading byte 36 blindly is the
    classic way this parser gets written wrong.
    """
    position = 12
    rate = channels = bits = 0
    frames = 0

    while position + 8 <= len(payload):
        name = payload[position:position + 4]
        try:
            size = struct.unpack("<I", payload[position + 4:position + 8])[0]
        except struct.error:
            break
        body = payload[position + 8:position + 8 + size]

        if name == b"fmt " and len(body) >= 16:
            _, channels, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
        elif name == b"data":
            frames = size
            break

        position += 8 + size + (size % 2)

    seconds = 0.0
    if rate and channels and bits and frames:
        seconds = frames / float(rate * channels * (bits // 8))

    return Media(
        container="wav", family="audio", seconds=seconds,
        sample_rate=rate, channels=channels,
        codec=f"pcm{bits}" if bits else "", confidence=0.97,
    )


def _mp4_duration(payload: bytes) -> float:
    """The `mvhd` atom's timescale and duration. Nested, so it is searched for.

    Walking the full atom tree would be the tidy approach and needs the whole file;
    `mvhd` is small and distinctive, so locating it directly gives a real duration
    from a header read rather than from a full parse.
    """
    index = payload.find(b"mvhd", 0, 4 * 1024 * 1024)
    if index < 0:
        return 0.0

    version = payload[index + 4:index + 5]
    try:
        if version == b"\x01":
            timescale, duration = struct.unpack(
                ">IQ", payload[index + 20:index + 32],
            )
        else:
            timescale, duration = struct.unpack(
                ">II", payload[index + 12:index + 20],
            )
    except struct.error:
        return 0.0

    return duration / float(timescale) if timescale else 0.0


def blocks(payload: bytes, uri: str) -> list[Block]:
    """A media file as a document: its metadata, and a stated absence.

    The `UNREADABLE` block is the important one. It carries the duration, so the
    gap says "45m 12s of audio, unheard" rather than "a file we skipped" — the
    first is a decision somebody can make, the second is noise.
    """
    found = sniff(payload)
    if found is None:
        return [unreadable(uri, "not a recognised media container")]

    described = Block(
        kind=BlockKind.SECTION,
        text=f"{found.container} {found.family}".strip(),
        locator=of_file(uri),
        attributes=found.to_dict(),
    )

    absent = Block(
        kind=BlockKind.UNREADABLE, text="", locator=of_file(uri),
        attributes={
            "reason": (
                f"{found.duration} of {found.family or 'media'} content is not "
                f"transcribed: the kernel identifies the container and reads its "
                f"metadata, and transcription is a ring 1 extractor. Nothing in "
                f"this file has been heard or seen"
            ),
            "duration": found.duration,
            "seconds": found.seconds,
            "needs": "transcription",
        },
    )
    return [described, absent]
