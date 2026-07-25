"""Media probes: images and video.

Binary media is not shapeless — it is *densely* shaped, just not in rows. What
the kernel needs from a PNG or an MP4 is its metadata (dimensions, duration,
codec container) plus a stable handle to the bytes, so a downstream
transformation can do the expensive work deliberately rather than as a side
effect of scanning a folder.

Headers are parsed natively. Pillow is used for images when it is installed,
because it covers formats and edge cases this module does not try to.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..meta import media_kind
from .base import BaseProbe, Capture, Target

#: SOFn markers carry image dimensions; SOF4/8/12 are not frame headers.
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


@dataclass(slots=True)
class MediaInfo:
    """What we could determine without decoding the payload."""

    kind: str = ""
    container: str = ""
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    frames: int = 0
    engine: str = "native"
    extra: dict[str, Any] = field(default_factory=dict)

    def to_record(self, target: Target) -> dict[str, Any]:
        return {
            "uri": target.uri,
            "filename": target.path.name if target.path else target.stem,
            "kind": self.kind,
            "container": self.container,
            "bytes": target.size,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": round(self.width / self.height, 4) if self.width and self.height else None,
            "pixels": self.width * self.height or None,
            "duration_s": round(self.duration_s, 3) or None,
            "frames": self.frames or None,
            "engine": self.engine,
            **self.extra,
        }


# --- image header parsing -----------------------------------------------


def image_size(head: bytes) -> tuple[int, int]:
    """Dimensions from an image header, ``(0, 0)`` when unrecognized."""
    if head.startswith(b"\x89PNG\r\n\x1a\n") and len(head) >= 24:
        width, height = struct.unpack(">II", head[16:24])
        return int(width), int(height)
    if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
        width, height = struct.unpack("<HH", head[6:10])
        return int(width), int(height)
    if head.startswith(b"BM") and len(head) >= 26:
        width, height = struct.unpack("<ii", head[18:26])
        return abs(int(width)), abs(int(height))
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return _webp_size(head)
    if head.startswith(b"\xff\xd8"):
        return _jpeg_size(head)
    return 0, 0


def _jpeg_size(data: bytes) -> tuple[int, int]:
    offset = 2
    end = len(data)
    while offset + 9 < end:
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if marker in _JPEG_SOF:
            height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
            return int(width), int(height)
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        length = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
        offset += 2 + length
    return 0, 0


def _webp_size(data: bytes) -> tuple[int, int]:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30:
        width, height = struct.unpack("<HH", data[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25:
        bits = struct.unpack("<I", data[21:25])[0]
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    return 0, 0


# --- video header parsing -----------------------------------------------


def mp4_info(path: Path, *, max_scan: int = 1 << 22) -> MediaInfo:
    """Duration and dimensions from an ISO-BMFF (MP4/MOV) file."""
    info = MediaInfo(kind="video", container="mp4")
    try:
        with path.open("rb") as handle:
            moov = _find_box(handle, b"moov", limit=max_scan)
            if moov is None:
                return info
            handle.seek(moov[0])
            payload = handle.read(min(moov[1], max_scan))
    except OSError:
        return info

    mvhd = _find_sub_box(payload, b"mvhd")
    if mvhd is not None:
        version = mvhd[0]
        if version == 1 and len(mvhd) >= 32:
            timescale, duration = struct.unpack(">IQ", mvhd[20:32])
        elif len(mvhd) >= 20:
            timescale, duration = struct.unpack(">II", mvhd[12:20])
        else:
            timescale = duration = 0
        if timescale:
            info.duration_s = duration / timescale
            info.extra["timescale"] = timescale

    tkhd = _find_sub_box(payload, b"tkhd")
    if tkhd is not None and len(tkhd) >= 84:
        offset = 84 if tkhd[0] == 1 else 76
        if len(tkhd) >= offset + 8:
            width, height = struct.unpack(">II", tkhd[offset : offset + 8])
            info.width, info.height = width >> 16, height >> 16
    return info


def avi_info(path: Path) -> MediaInfo:
    """Duration and dimensions from a RIFF/AVI header."""
    info = MediaInfo(kind="video", container="avi")
    try:
        head = path.open("rb").read(4096)
    except OSError:
        return info
    index = head.find(b"avih")
    if index == -1 or len(head) < index + 48:
        return info
    body = head[index + 8 : index + 48]
    micros_per_frame, _, _, _, total_frames = struct.unpack("<IIIII", body[:20])
    width, height = struct.unpack("<II", body[32:40])
    info.width, info.height = int(width), int(height)
    info.frames = int(total_frames)
    if micros_per_frame:
        info.duration_s = total_frames * micros_per_frame / 1e6
        info.extra["fps"] = round(1e6 / micros_per_frame, 3)
    return info


def _find_box(handle: Any, name: bytes, *, limit: int) -> tuple[int, int] | None:
    """Locate a top-level ISO-BMFF box, returning ``(payload_offset, size)``."""
    offset = 0
    while offset < limit:
        handle.seek(offset)
        header = handle.read(8)
        if len(header) < 8:
            return None
        size = struct.unpack(">I", header[:4])[0]
        box = header[4:8]
        if size == 1:  # 64-bit extended size
            extended = handle.read(8)
            if len(extended) < 8:
                return None
            size = struct.unpack(">Q", extended)[0]
            header_len = 16
        elif size == 0:  # box runs to end of file
            return (offset + 8, limit - offset) if box == name else None
        else:
            header_len = 8
        if size < header_len:
            return None
        if box == name:
            return offset + header_len, size - header_len
        offset += size
    return None


def _find_sub_box(payload: bytes, name: bytes) -> bytes | None:
    """Find a box anywhere inside an already-read parent payload."""
    index = payload.find(name)
    if index < 4:
        return None
    try:
        size = struct.unpack(">I", payload[index - 4 : index])[0]
    except struct.error:
        return None
    start = index + 4
    end = index - 4 + size if size > 8 else len(payload)
    return payload[start : min(end, len(payload))]


# --- probes --------------------------------------------------------------


class ImageProbe(BaseProbe):
    """Images become one metadata record plus a handle to the bytes."""

    name = "image"
    suffixes = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff")

    def sniff(self, target: Target) -> float:
        kind = media_kind(target.head)
        if kind.startswith("image/"):
            return 0.95
        return 0.7 if target.suffix in self.suffixes else 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        capture = self._capture(target)
        if target.path is None:
            capture.warnings.append("image probe needs a local path")
            return capture
        capture.bytes_read = len(target.head)

        info = MediaInfo(kind="image", container=media_kind(target.head) or target.suffix.lstrip("."))
        try:
            from PIL import Image  # noqa: PLC0415 - optional

            with Image.open(target.path) as image:
                info.width, info.height = image.size
                info.engine = "pillow"
                info.extra["mode"] = image.mode
                info.frames = getattr(image, "n_frames", 1)
        except ImportError:
            info.width, info.height = image_size(target.head)
        except Exception as exc:  # noqa: BLE001 - corrupt files must not stop a scan
            capture.warnings.append(f"pillow could not read image: {exc}")
            info.width, info.height = image_size(target.head)

        if not info.width:
            capture.warnings.append("could not determine image dimensions")
        capture.payloads.append(self._wrap([info.to_record(target)], target, name=f"{target.entity}_image"))
        return capture


class VideoProbe(BaseProbe):
    """Video containers become one metadata record."""

    name = "video"
    suffixes = (".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm")

    def sniff(self, target: Target) -> float:
        kind = media_kind(target.head)
        if kind.startswith("video/"):
            return 0.95
        if target.head[4:8] == b"ftyp":
            return 0.95
        return 0.7 if target.suffix in self.suffixes else 0.0

    def capture(self, target: Target, *, limit: int = 0) -> Capture:
        capture = self._capture(target)
        if target.path is None:
            capture.warnings.append("video probe needs a local path")
            return capture
        capture.bytes_read = len(target.head)

        if target.head[4:8] == b"ftyp" or target.suffix in (".mp4", ".m4v", ".mov"):
            info = mp4_info(target.path)
        elif target.head.startswith(b"RIFF") or target.suffix == ".avi":
            info = avi_info(target.path)
        else:
            info = MediaInfo(kind="video", container=media_kind(target.head) or target.suffix.lstrip("."))
            capture.warnings.append(
                f"{info.container or 'unknown'} container recognized but not parsed; "
                "metadata limited to file-level facts"
            )
        capture.payloads.append(self._wrap([info.to_record(target)], target, name=f"{target.entity}_video"))
        return capture
