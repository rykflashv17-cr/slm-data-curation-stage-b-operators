from __future__ import annotations

from pathlib import Path

import numpy as np
from mcap.writer import Writer

from stage_b_mcap_v3.contracts import CleaningDecision, McapEpisodeRef, QualityEpisode, StatisticsBundle
from stage_b_mcap_v3.operators import GripperGapOperator, judge_one
from stage_b_mcap_v3.spec import GripperGapConfig, McapSpec


def test_gripper_gap_marks_only_missing_section_and_judges_both_sides(
    tmp_path: Path, monkeypatch,
) -> None:
    mcap = tmp_path / "episode.mcap"
    frame_times = np.arange(10, dtype=np.int64) * 1_000_000_000
    with mcap.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        left = writer.register_channel("/left", "raw", 0)
        right = writer.register_channel("/right", "raw", 0)
        for i, timestamp in enumerate(frame_times):
            if i not in (4, 5):
                writer.add_message(left, int(timestamp), b"width", int(timestamp))
            writer.add_message(right, int(timestamp), b"width", int(timestamp))
        writer.finish()
    ref = McapEpisodeRef(0, tmp_path, mcap, ".")
    episode = QualityEpisode(
        ref, np.zeros((10, 2)), np.zeros((10, 2)), frame_times,
        np.zeros((10, 2), dtype=bool), np.zeros((10, 2), dtype=bool),
    )
    spec = McapSpec(
        tmp_path, tmp_path, tmp_path / "quality.yaml", tmp_path, tmp_path,
        ("/reference",), (), ("train",), 1, {},
        GripperGapConfig("/left", "/right", 600_000_000),
    )
    gaps = GripperGapOperator().run(episode, spec)
    np.testing.assert_array_equal(gaps.valid_mask, [True] * 4 + [False] * 2 + [True] * 4)
    assert len(gaps.invalid_ranges) == 1
    assert gaps.invalid_ranges[0]["side"] == "left"
    assert gaps.invalid_ranges[0]["start_frame"] == 4
    assert gaps.invalid_ranges[0]["end_frame_exclusive"] == 6

    calls = []

    def judge_segment(segment, _cfg, _stats):
        calls.append(segment.frame_times_ns.copy())
        keep = np.ones(segment.num_frames, dtype=bool)
        keep[-1] = False
        return CleaningDecision(segment.ref, segment.frame_times_ns, keep, False, (), {
            "stage1_flagged_frames": 0, "stage2_da_mean": 1.0,
            "stage3_excluded_frames": 0, "stage4_removed_frames": 0,
        })

    monkeypatch.setattr("stage_b_mcap_v3.operators._judge_valid_segment", judge_segment)
    statistics = StatisticsBundle(None, None, None, 0, tmp_path)
    decision = judge_one(episode, None, statistics, gaps)
    assert [len(times) for times in calls] == [4, 4]
    np.testing.assert_array_equal(
        decision.keep_mask, [True, True, True, False, False, False, True, True, True, False]
    )
    assert not decision.discard_episode
    assert decision.metrics["gripper_gap_invalid_frames"] == 2
    assert decision.gripper_gap_ranges == gaps.invalid_ranges


def test_gripper_gap_marks_whole_episode_when_a_side_has_no_samples(tmp_path: Path) -> None:
    mcap = tmp_path / "episode.mcap"
    with mcap.open("wb") as stream:
        writer = Writer(stream)
        writer.start()
        right = writer.register_channel("/right", "raw", 0)
        for i in range(3):
            writer.add_message(right, i * 1_000_000_000, b"width", i * 1_000_000_000)
        writer.finish()
    ref = McapEpisodeRef(0, tmp_path, mcap, ".")
    episode = QualityEpisode(
        ref, np.zeros((3, 2)), np.zeros((3, 2)),
        np.arange(3, dtype=np.int64) * 1_000_000_000,
        np.zeros((3, 2), dtype=bool), np.zeros((3, 2), dtype=bool),
    )
    spec = McapSpec(
        tmp_path, tmp_path, tmp_path / "quality.yaml", tmp_path, tmp_path,
        ("/reference",), (), ("train",), 1, {},
        GripperGapConfig("/left", "/right", 600_000_000),
    )
    gaps = GripperGapOperator().run(episode, spec)
    assert not gaps.valid_mask.any()
    assert gaps.invalid_ranges[0]["side"] == "left"
    assert gaps.invalid_ranges[0]["max_nearest_gap_seconds"] is None
