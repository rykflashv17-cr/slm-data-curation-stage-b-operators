"""Apply one Stage B frame decision to all MCAP topics and write a new MCAP."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .contracts import CleaningDecision
from .spec import McapSpec
from .video import PreparedVideo, reencode_selected_video


def keep_at_times(frame_times_ns: np.ndarray, keep_mask: np.ndarray, message_times_ns: np.ndarray) -> np.ndarray:
    """Assign each asynchronous message to its nearest quality frame."""
    if frame_times_ns.ndim != 1 or keep_mask.shape != frame_times_ns.shape:
        raise ValueError("invalid quality timeline/mask")
    if not len(frame_times_ns):
        return np.zeros(len(message_times_ns), dtype=bool)
    if np.any(np.diff(frame_times_ns) < 0):
        raise ValueError("quality timestamps must not go backward")
    edges = frame_times_ns[:-1] + np.diff(frame_times_ns) // 2
    positions = np.searchsorted(edges, np.asarray(message_times_ns, dtype=np.int64), side="right")
    return np.asarray(keep_mask, dtype=bool)[positions]


@dataclass(frozen=True)
class _TimeSelector:
    edges: np.ndarray
    mask: np.ndarray

    @classmethod
    def from_decision(cls, decision: CleaningDecision) -> "_TimeSelector":
        times = decision.frame_times_ns
        if not len(times):
            raise ValueError("cannot export a decision without quality timestamps")
        return cls(times[:-1] + np.diff(times) // 2, decision.keep_mask.astype(bool))

    def keep(self, timestamp_ns: int) -> bool:
        return bool(self.mask[np.searchsorted(self.edges, int(timestamp_ns), side="right")])


def _has_interior_cut(mask: np.ndarray) -> bool:
    kept = np.flatnonzero(mask)
    return bool(kept.size and not np.all(mask[: int(kept[-1]) + 1]))


@dataclass
class _VideoSource:
    raw_path: Path
    codec: str
    proto_class: Any
    selected: list[bool] = field(default_factory=list)
    prepared: PreparedVideo | None = None
    cursor: int = 0


def _prepare_videos(source: Path, decision: CleaningDecision, temp_root: Path) -> dict[int, _VideoSource]:
    """Only videos with an interior cut need a new independently decodable stream."""
    if not _has_interior_cut(decision.keep_mask):
        return {}
    from mcap.reader import make_reader
    from mcap_protobuf.decoder import DecoderFactory

    topics: set[str] = set()
    with source.open("rb") as stream:
        for schema, channel, _message in make_reader(stream).iter_messages():
            if schema is not None and "CompressedVideo" in schema.name:
                topics.add(channel.topic)
    if not topics:
        return {}
    selector = _TimeSelector.from_decision(decision)
    videos: dict[int, _VideoSource] = {}
    handles: dict[int, Any] = {}
    try:
        with source.open("rb") as stream:
            reader = make_reader(stream, decoder_factories=[DecoderFactory()])
            for schema, channel, message, decoded in reader.iter_decoded_messages(topics=sorted(topics)):
                if schema is None or "CompressedVideo" not in schema.name:
                    continue
                ident = int(channel.id)
                codec = str(getattr(decoded, "format", "") or "h264").lower()
                if ident not in videos:
                    raw_path = temp_root / f"source_video_{ident}.{'h265' if codec in ('h265', 'hevc') else 'h264'}"
                    videos[ident] = _VideoSource(raw_path, codec, type(decoded))
                    handles[ident] = raw_path.open("wb")
                video = videos[ident]
                if video.codec != codec:
                    raise ValueError(f"video codec changes within channel {channel.topic}")
                handles[ident].write(bytes(decoded.data))
                video.selected.append(selector.keep(message.log_time))
    finally:
        for handle in handles.values():
            handle.close()
    for ident, video in videos.items():
        if not any(video.selected):
            continue
        if all(video.selected):
            continue
        target = temp_root / f"clean_video_{ident}.{'h265' if video.codec in ('h265', 'hevc') else 'h264'}"
        video.prepared = reencode_selected_video(
            video.raw_path, target, video.selected, video.codec,
        )
    return videos


def _output_path(decision: CleaningDecision, spec: McapSpec) -> Path:
    rel = Path(decision.ref.rel_path)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe episode rel_path: {rel}")
    return spec.output_root / rel / decision.ref.mcap_path.name


def _write_selected(
    source: Path, target: Path, decision: CleaningDecision,
    static_topics: tuple[str, ...], videos: dict[int, _VideoSource],
) -> tuple[int, int]:
    from mcap.reader import make_reader
    from mcap.writer import Writer

    schema_ids: dict[int, int] = {}
    channel_ids: dict[int, int] = {}
    total = written = 0
    selector = _TimeSelector.from_decision(decision)
    video_handles = {
        ident: video.prepared.path.open("rb")
        for ident, video in videos.items() if video.prepared is not None
    }
    try:
        with source.open("rb") as input_stream, target.open("wb") as output_stream:
            reader = make_reader(input_stream)
            writer = Writer(output_stream)
            writer.start()
            for schema, channel, message in reader.iter_messages():
                total += 1
                if schema is not None and schema.id not in schema_ids:
                    schema_ids[schema.id] = writer.register_schema(
                        name=schema.name, encoding=schema.encoding, data=schema.data
                    )
                if channel.id not in channel_ids:
                    channel_ids[channel.id] = writer.register_channel(
                        topic=channel.topic,
                        message_encoding=channel.message_encoding,
                        schema_id=schema_ids[schema.id] if schema is not None else 0,
                        metadata=dict(channel.metadata),
                    )
                keep = channel.topic in static_topics or selector.keep(message.log_time)
                if not keep:
                    continue
                payload = message.data
                video = videos.get(int(channel.id))
                if video is not None and video.prepared is not None:
                    obj = video.proto_class()
                    obj.ParseFromString(message.data)
                    start, end = video.prepared.offsets[video.cursor]
                    handle = video_handles[int(channel.id)]
                    handle.seek(start)
                    obj.data = handle.read(end - start)
                    payload = obj.SerializeToString()
                    video.cursor += 1
                writer.add_message(
                    channel_id=channel_ids[channel.id], log_time=message.log_time,
                    publish_time=message.publish_time, sequence=message.sequence,
                    data=payload,
                )
                written += 1
            for metadata in reader.iter_metadata():
                writer.add_metadata(metadata.name, dict(metadata.metadata))
            for attachment in reader.iter_attachments():
                writer.add_attachment(
                    create_time=attachment.create_time, log_time=attachment.log_time,
                    name=attachment.name, media_type=attachment.media_type,
                    data=attachment.data,
                )
            writer.finish()
    finally:
        for handle in video_handles.values():
            handle.close()
    for video in videos.values():
        if video.prepared is not None and video.cursor != len(video.prepared.offsets):
            raise RuntimeError("not all re-encoded video frames were written")
    return total, written


def _validate_mcap(path: Path, expected_messages: int) -> None:
    from mcap.reader import make_reader

    with path.open("rb") as stream:
        count = sum(1 for _ in make_reader(stream).iter_messages())
    if count != expected_messages:
        raise RuntimeError(f"output MCAP message count {count} != expected {expected_messages}")


def export_mcap(decision: CleaningDecision, spec: McapSpec, *, overwrite: bool = False) -> Path | None:
    """Write atomically; never modify the source MCAP."""
    target = _output_path(decision, spec)
    source = decision.ref.mcap_path
    if source.resolve() == target.resolve():
        raise ValueError("output MCAP path must differ from the source")
    if target.exists() and not overwrite:
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    audit = {
        "source": str(source), "output": None if decision.discard_episode else str(target),
        "episode_index": decision.ref.episode_index,
        "discard_episode": decision.discard_episode,
        "reasons": list(decision.reasons),
        "metrics": decision.metrics,
        "gripper_gap_ranges": list(decision.gripper_gap_ranges),
        "quality_frames": int(len(decision.keep_mask)),
        "kept_quality_frames": int(np.count_nonzero(decision.keep_mask)),
    }
    audit_path = target.with_suffix(".audit.json")
    if decision.discard_episode or not np.any(decision.keep_mask):
        if target.exists():
            target.unlink()  # --overwrite explicitly authorized replacement of this v3 output.
        audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
        return None
    temp_base = spec.output_root / ".tmp"
    temp_base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stageb_v3_", dir=temp_base) as temp_name:
        temp = Path(temp_name)
        videos = _prepare_videos(source, decision, temp)
        partial = temp / "episode.mcap"
        total, written = _write_selected(source, partial, decision, spec.static_topics, videos)
        _validate_mcap(partial, written)
        audit.update({
            "source_messages": total, "output_messages": written,
            "video_channels_reencoded": sum(v.prepared is not None for v in videos.values()),
        })
        os.replace(partial, target)
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    return target


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)
