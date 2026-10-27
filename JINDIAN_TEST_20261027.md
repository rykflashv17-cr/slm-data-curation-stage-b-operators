# Jindian Marvin Stage B v3 test — 2026-10-27

## Gripper-gap operator update

The baseline table below is from before the gap operator was added. The
source MCAPs and its original report are unchanged. The new
`GripperGapOperator` checks each quality frame against measured left/right
gripper topic timestamps using a 0.6 s nearest-sample limit. Its output is a
per-side invalid-frame mask and exact intervals. Stage1–4 run independently
on each contiguous valid section; the MCAP exporter uses the combined mask.

A new quality-only smoke run of the three previously unjudged MCAPs is under
`../jindian_stage_b_v3_test/gripper_gap_smoke_20261027`. All three were
judged with zero input failures:

| Episode | Gripper-invalid quality frames | Other frames removed | Kept frames |
|---|---:|---:|---:|
| `150745_a94d34f9` | 5 | 0 | 500 / 505 |
| `151153_c8aecfba` | 2 | 36 (short leading section rejected by Stage2/minimum length) | 525 / 563 |
| `154413_d913bff1` | 253 | 0 | 293 / 546 |

The full 88-episode rerun recomputed statistics after the adapter change and
judged all 88 episodes with zero input failures: 80 fully retained, 6 partly
removed, and 2 wholly discarded. It marks 260 quality frames invalid for
gripper gaps across the three affected episodes. The total removed time is
72.579 s, including all Stage1–4 and gripper-gap decisions. The current full
report is `../jindian_stage_b_v3_test/gripper_gap_full_quality_20261027/decisions.jsonl`.
No video or cleaned MCAP was produced by this full quality-only run.

One resulting MCAP for `154413_d913bff1` was exported to
`../jindian_stage_b_v3_test/gripper_gap_export_20261027`; it is readable,
contains 12,240 messages and three re-encoded video channels. The exporter
decoded each new video stream during its own validation. The three-episode
smoke statistics were used only for the local smoke check; the full report
above uses statistics recomputed from all 88 episodes.

## Scope and profile

- Source: `/Users/flashryk/Desktop/jindian_test-简智夹爪`, 88 MCAP files, 7.0 GiB on disk.
- All 88 MCAP summaries contain both arm joint-state and joint-target topics plus three H.264 video channels.
- The added adapter reads 14 arm joints and two gripper widths, aligns joint targets to measured states, and evaluates every second 50 Hz robot sample at 25 Hz.
- This is a provisional **joint and gripper** quality profile. TCP pose, status validity flags, tactile data and video content are not scored. Stage 1/3 thresholds were seeded from the ABC-130K profile and have not been calibrated to Marvin.
- Source MCAPs were read only. The quality run wrote reports and caches under `../jindian_stage_b_v3_test/full_quality`.

## Baseline full quality run before the gripper-gap operator

| Outcome | Episodes | Removed quality time |
|---|---:|---:|
| Full keep | 80 | 0 s |
| Stage 3 triggered a tail trim, episode retained | 3 | 16.241 s |
| Stage 2 discarded the entire episode for inconsistent per-joint lag | 2 | 44.440 s |
| Input error: gripper state gap > 0.6 s | 3 | No decision |
| **Total** | **88** | **60.681 s among 85 evaluated episodes** |

Stage 1 flagged zero frames; Stage 4 removed zero frames. Stage 3 flagged 88 frames across the evaluated episodes, and the upstream prefix-truncation policy then removed 406 frames from the three affected tails. Stage 2 discarded 1,111 frames from two complete episodes. Thus 1,517 of 43,897 evaluated quality frames were marked for removal. This is a technical test result with the provisional profile, not an approved dataset cleaning decision.

The three input errors are `episode_150745_a94d34f9.h264.mcap`, `episode_151153_c8aecfba.h264.mcap` and `episode_154413_d913bff1.h264.mcap`. The largest nearest-sample gaps are about 0.80 s, 0.63 s and 7.35 s respectively. Their missing gripper data was not silently filled for quality judgment.

`../jindian_stage_b_v3_test/full_quality/decisions.jsonl` records the baseline outcomes. An earlier 10-episode smoke test exists at the parent test directory. For the current full-run results, use `gripper_gap_full_quality_20261027`.

The local review page at `../jindian_stage_b_v3_review_20261027/index.html` shows all 88 episodes with compressed original-video previews and interactive kept/removed timelines. Its 88 previews all passed H.264 decode checks. Keep its `media/` directory beside the HTML file when moving the review page.

## MCAP output checks

- An actual Stage 3 decision on `episode_144308_9aa17ef1.h264.mcap` retained 338 of 466 quality frames. The exported MCAP is under `../jindian_stage_b_v3_test/full_quality/export_one_real_decision/`. It has 13,456 readable messages, is 55.7 MB versus the 78.6 MB source, and all three video streams decode.
- A separate **manual exporter test** removed quality frames 200–209 from the same source to force an interior cut. The output is under `../jindian_stage_b_v3_test/manual_interior_cut/`. It has 18,415 readable messages; all three H.264 channels were re-encoded and decoded successfully. It is 84.7 MB. This manual mask is not a Stage B quality decision.
- The current v3 test suite passed: 5 tests.

Run quality only with `python3 run.py quality --config configs/jindian_marvin.yaml --output-root /path/to/report`. Run MCAP export with `run` after reviewing the profile and report. The output path must differ from the source dataset.
