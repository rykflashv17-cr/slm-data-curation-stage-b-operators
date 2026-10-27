from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pytest
from mcap.reader import make_reader
from mcap.writer import Writer

from stage_b_mcap_v3.contracts import CleaningDecision, McapEpisodeRef
from stage_b_mcap_v3.mcap_export import export_mcap, keep_at_times
from stage_b_mcap_v3.spec import McapSpec
from stage_b_mcap_v3.video import _aud_offsets


def _spec(root: Path, output: Path) -> McapSpec:
    return McapSpec(root, root, root, root, output, ("/top-camera",), ("/calibration",), ("train",), 1)


def _decision(root: Path, mcap: Path, mask: list[int], *, spacing_ns: int = 100) -> CleaningDecision:
    ref = McapEpisodeRef(0, root, mcap, "episode_000", "train", "task")
    return CleaningDecision(
        ref, np.arange(len(mask), dtype=np.int64) * spacing_ns,
        np.asarray(mask, dtype=np.int8), False, (), {},
    )


def test_time_mapping_and_nonvideo_export(tmp_path: Path) -> None:
    source = tmp_path / "source" / "episode_000" / "episode.mcap"
    source.parent.mkdir(parents=True)
    with source.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        schema = writer.register_schema("test.State", "jsonschema", b"{}")
        data_channel = writer.register_channel("/state", "json", schema)
        static_channel = writer.register_channel("/calibration", "json", schema)
        for i in range(4):
            writer.add_message(data_channel, i * 100, f'{{"frame":{i}}}'.encode(), i * 100)
        writer.add_message(static_channel, 150, b'{}', 150)
        writer.add_metadata("source", {"name": "synthetic"})
        writer.finish()
    decision = _decision(tmp_path / "source", source, [1, 0, 1, 0])
    assert keep_at_times(decision.frame_times_ns, decision.keep_mask, np.array([49, 50, 149, 150])).tolist() == [True, False, False, True]
    target = export_mcap(decision, _spec(tmp_path / "source", tmp_path / "output"))
    assert target is not None and target.is_file() and source.is_file()
    with target.open("rb") as stream:
        reader = make_reader(stream)
        rows = [(channel.topic, message.log_time, message.data) for _, channel, message in reader.iter_messages()]
        meta = [(record.name, record.metadata) for record in reader.iter_metadata()]
    assert rows == [
        ("/state", 0, b'{"frame":0}'),
        ("/calibration", 150, b'{}'),
        ("/state", 200, b'{"frame":2}'),
    ]
    assert meta == [("source", {"name": "synthetic"})]
    audit = json.loads(target.with_suffix(".audit.json").read_text())
    assert audit["video_channels_reencoded"] == 0


def _video_proto():
    from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

    file_desc = descriptor_pb2.FileDescriptorProto()
    file_desc.name = "test_video.proto"
    file_desc.package = "test"
    file_desc.syntax = "proto3"
    message = file_desc.message_type.add()
    message.name = "CompressedVideo"
    field = message.field.add()
    field.name, field.number, field.type = "data", 1, field.TYPE_BYTES
    field = message.field.add()
    field.name, field.number, field.type = "format", 2, field.TYPE_STRING
    file_set = descriptor_pb2.FileDescriptorSet()
    file_set.file.add().CopyFrom(file_desc)
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_desc)
    klass = message_factory.GetMessageClass(pool.FindMessageTypeByName("test.CompressedVideo"))
    return klass, file_set.SerializeToString()


def test_h264_interior_cut_reencodes_decodable_mcap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ffmpeg").symlink_to(ffmpeg)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    original_video = tmp_path / "original.h264"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=32x32:rate=5",
        "-frames:v", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-x264-params", "aud=1:repeat-headers=1:bframes=0",
        "-f", "h264", str(original_video),
    ], check=True)
    payload = original_video.read_bytes()
    units = _aud_offsets(original_video, "h264")
    assert len(units) == 8
    klass, descriptor = _video_proto()
    source = tmp_path / "source" / "episode_000" / "episode.mcap"
    source.parent.mkdir(parents=True)
    with source.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        schema = writer.register_schema("test.CompressedVideo", "protobuf", descriptor)
        channel = writer.register_channel("/top-camera", "protobuf", schema)
        for i, (start, end) in enumerate(units):
            encoded = klass(data=payload[start:end], format="h264").SerializeToString()
            writer.add_message(channel, i * 200_000_000, encoded, i * 200_000_000)
        writer.finish()
    decision = _decision(
        tmp_path / "source", source, [1, 1, 0, 0, 1, 1, 1, 1],
        spacing_ns=200_000_000,
    )
    target = export_mcap(decision, _spec(tmp_path / "source", tmp_path / "output"))
    assert target is not None
    from mcap_protobuf.decoder import DecoderFactory

    with target.open("rb") as stream:
        reader = make_reader(stream, decoder_factories=[DecoderFactory()])
        rows = [(message.log_time, bytes(decoded.data)) for _, _, message, decoded in reader.iter_decoded_messages()]
    assert [time for time, _ in rows] == [0, 200_000_000, 800_000_000, 1_000_000_000, 1_200_000_000, 1_400_000_000]
    output_video = tmp_path / "output.h264"
    output_video.write_bytes(b"".join(data for _, data in rows))
    assert len(_aud_offsets(output_video, "h264")) == 6
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "h264",
        "-i", str(output_video), "-f", "null", "-",
    ], check=True)
    audit = json.loads(target.with_suffix(".audit.json").read_text())
    assert audit["video_channels_reencoded"] == 1
