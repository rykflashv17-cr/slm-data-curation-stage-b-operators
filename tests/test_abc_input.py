from __future__ import annotations

from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory
from mcap.writer import Writer

from stage_b_mcap_v3.contracts import McapEpisodeRef
from stage_b_mcap_v3.cli import main
from stage_b_mcap_v3.operators import GlobalStatsOperator, McapInputOperator, judge_one
from stage_b_mcap_v3.spec import McapSpec, build_adapter, load_spec, quality_config


def _proto_types():
    file_desc = descriptor_pb2.FileDescriptorProto()
    file_desc.name = "source_test.proto"
    file_desc.package = "source_test"
    file_desc.syntax = "proto3"
    state = file_desc.message_type.add()
    state.name = "RobotState"
    field = state.field.add()
    field.name, field.number, field.type, field.label = "position", 1, field.TYPE_DOUBLE, field.LABEL_REPEATED
    field = state.field.add()
    field.name, field.number, field.type, field.label = "pose", 2, field.TYPE_DOUBLE, field.LABEL_REPEATED
    video = file_desc.message_type.add()
    video.name = "CompressedVideo"
    field = video.field.add()
    field.name, field.number, field.type = "data", 1, field.TYPE_BYTES
    field = video.field.add()
    field.name, field.number, field.type = "format", 2, field.TYPE_STRING
    pool = descriptor_pool.DescriptorPool()
    pool.Add(file_desc)
    classes = {
        name: message_factory.GetMessageClass(pool.FindMessageTypeByName(f"source_test.{name}"))
        for name in ("RobotState", "CompressedVideo")
    }
    descriptor_set = descriptor_pb2.FileDescriptorSet()
    descriptor_set.file.add().CopyFrom(file_desc)
    return classes, descriptor_set.SerializeToString()


def test_abc_mcap_input_uses_original_reference_clock(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[4] / "github" / "SLM_data_curation"
    package = repo / "datasets" / "abc_130k"
    root = tmp_path / "source"
    episode = root / "data" / "train" / "pick" / "episode_000"
    episode.mkdir(parents=True)
    mcap = episode / "episode.mcap"
    classes, descriptor = _proto_types()
    video_topics = ("/top-camera",)
    arm_topics = ("/left-arm-state", "/right-arm-state", "/left-arm-action", "/right-arm-action")
    gripper_topics = ("/left-ee-state", "/right-ee-state", "/left-ee-action", "/right-ee-action")
    times = np.arange(20, dtype=np.int64) * 33_333_333 + 1_000_000_000
    with mcap.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        state_schema = writer.register_schema("source_test.RobotState", "protobuf", descriptor)
        video_schema = writer.register_schema("source_test.CompressedVideo", "protobuf", descriptor)
        channels = {
            topic: writer.register_channel(
                topic, "protobuf", video_schema if topic in video_topics else state_schema
            )
            for topic in (*video_topics, *arm_topics, *gripper_topics)
        }
        for i, timestamp in enumerate(times):
            timestamp = int(timestamp)
            for topic in arm_topics:
                payload = classes["RobotState"](position=[0.01 * i] * 6).SerializeToString()
                writer.add_message(channels[topic], timestamp, payload, timestamp)
            for topic in gripper_topics:
                payload = classes["RobotState"](position=[0.3]).SerializeToString()
                writer.add_message(channels[topic], timestamp, payload, timestamp)
            payload = classes["CompressedVideo"](data=b"synthetic", format="h264").SerializeToString()
            writer.add_message(channels["/top-camera"], timestamp, payload, timestamp)
        writer.finish()
    eef_topics = (
        "/left-arm-state", "/right-arm-state", "/left-arm-action", "/right-arm-action"
    )
    rows = [
        (topic, int(timestamp), np.eye(4, dtype=np.float64).reshape(-1).tolist())
        for topic in eef_topics for timestamp in times
    ]
    pq.write_table(pa.table({
        "topic": [row[0] for row in rows],
        "timestamp_ns": [row[1] for row in rows],
        "pose": [row[2] for row in rows],
    }), episode / "eef_pose.parquet")
    spec = McapSpec(
        repo, package, package / "quality_stage_b.yaml", root, tmp_path / "output",
        ("/top-camera",), (), ("train",), 1,
    )
    cfg = quality_config(spec)
    adapter = build_adapter(spec)
    ref = McapEpisodeRef(0, root, mcap, "data/train/pick/episode_000", "train", "pick")
    result = McapInputOperator().run(ref, spec, cfg, adapter)
    assert result.state.shape == (20, 32)
    assert result.action.shape == (20, 32)
    np.testing.assert_array_equal(result.frame_times_ns, times)
    assert result.state[0, 12] == np.float64(np.float32(0.7))
    stats = GlobalStatsOperator().run(spec, cfg, [ref], recompute=True)
    assert stats.global_stats.num_frames == 20
    assert stats.stage1_stats.num_channels == cfg.schema.stage1_num_channels
    decision = judge_one(result, cfg, stats)
    assert decision.keep_mask.shape == (20,)
    assert decision.discard_episode  # 20 frames is shorter than ABC Stage B's 120-frame minimum.
    config = Path(__file__).resolve().parents[1] / "configs" / "abc_130k.yaml"
    configured = load_spec(config, input_root=root, output_root=spec.output_root)
    assert quality_config(configured).stage2.fill_missing_action_eef is False
    assert main([
        "quality", "--config", str(config),
        "--input-root", str(root), "--output-root", str(spec.output_root),
        "--sample-size", "1",
    ]) == 0
    assert (spec.output_root / "decisions.jsonl").is_file()
    assert main([
        "quality", "--config", str(config),
        "--input-root", str(root), "--output-root", str(spec.output_root),
        "--mcap", str(mcap),
    ]) == 0
