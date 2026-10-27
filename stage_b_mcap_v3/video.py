"""Rebuild inter-frame compressed MCAP video after interior frame removal.

The first implementation deliberately re-encodes the surviving video frames.
For a pure prefix trim, the exporter copies the original encoded packets.
"""

from __future__ import annotations

import mmap
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _removed_runs(keep: list[bool]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    for i, value in enumerate([*keep, True]):
        if not value and start is None:
            start = i
        elif value and start is not None:
            runs.append((start, i - 1))
            start = None
    return runs


def _select_filter(keep: list[bool]) -> str:
    ranges = _removed_runs(keep)
    if not ranges:
        return "select=1"
    terms = [f"between(n\\,{start}\\,{end})" for start, end in ranges]
    return f"select=not({'+'.join(terms)})"


def _aud_offsets(path: Path, codec: str) -> list[tuple[int, int]]:
    """Find access units emitted with FFmpeg's aud=1 encoder option."""
    hevc = codec in ("h265", "hevc")
    with path.open("rb") as file:
        with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
            starts: list[int] = []
            cursor = 0
            while True:
                index = data.find(b"\x00\x00\x01", cursor)
                if index < 0:
                    break
                payload = index + 3
                if payload < len(data):
                    kind = ((data[payload] >> 1) & 0x3F) if hevc else (data[payload] & 0x1F)
                    if kind == (35 if hevc else 9):
                        starts.append(index)
                cursor = payload + 1
            if not starts:
                raise RuntimeError(f"encoder produced no AUD-delimited access units: {path}")
            return [(0 if i == 0 else starts[i], starts[i + 1] if i + 1 < len(starts) else len(data))
                    for i in range(len(starts))]


@dataclass(frozen=True)
class PreparedVideo:
    path: Path
    offsets: tuple[tuple[int, int], ...]
    codec: str

    def frame(self, index: int) -> bytes:
        start, end = self.offsets[index]
        with self.path.open("rb") as stream:
            stream.seek(start)
            return stream.read(end - start)


def reencode_selected_video(
    source_annexb: Path, target_annexb: Path, keep: list[bool], codec: str,
) -> PreparedVideo:
    """Decode the source stream, select frames, encode a valid new Annex-B stream."""
    codec = codec.lower()
    if codec not in ("h264", "avc", "h265", "hevc"):
        raise ValueError(f"interior video deletion needs a supported codec, got {codec!r}")
    normalized = "hevc" if codec in ("h265", "hevc") else "h264"
    encoder = "libx265" if normalized == "hevc" else "libx264"
    options = "aud=1:repeat-headers=1:bframes=0:keyint=10:min-keyint=10"
    if not any(keep):
        raise ValueError("cannot re-encode an empty video stream")
    filter_script = target_annexb.with_suffix(".filter")
    filter_script.write_text(f"[0:v]{_select_filter(keep)}[selected]", encoding="utf-8")
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", normalized, "-i", str(source_annexb),
        "-filter_complex_script", str(filter_script), "-map", "[selected]",
        "-fps_mode", "passthrough", "-an",
        "-c:v", encoder, "-crf", "18", "-threads", "1",
        "-x265-params" if normalized == "hevc" else "-x264-params", options,
        "-f", normalized, str(target_annexb),
    ]
    subprocess.run(command, check=True, capture_output=True)
    if not target_annexb.is_file() or target_annexb.stat().st_size == 0:
        raise RuntimeError(f"empty re-encoded video: {target_annexb}")
    offsets = _aud_offsets(target_annexb, codec)
    if len(offsets) != sum(keep):
        raise RuntimeError(
            f"video frame count changed: expected {sum(keep)}, encoded {len(offsets)}"
        )
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", normalized,
         "-i", str(target_annexb), "-f", "null", "-"],
        check=True, capture_output=True,
    )
    return PreparedVideo(target_annexb, tuple(offsets), codec)
