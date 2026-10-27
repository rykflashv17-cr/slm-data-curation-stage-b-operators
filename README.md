# Stage B v3: MCAP → cleaned MCAP

This independent v3 package has **nine registered operators**. Stage1, Stage2,
Stage3 and Stage4 are separate callable classes. `build_prefix` calculates
the valid prefix before Stage4, and `judge_one` composes the stages on each
sensor-valid segment. These two functions are not registered operators.
The quality decision uses the current upstream Stage B Stage1–4 functions and
the same dataset quality YAML. No Stage A dataset is written.
The integration is pinned to upstream commit
`0e694dbc9706b2a5bac8e550f8cd9de18f5b7473`; use that revision's
`qwen-manip-preprocess` engine and dataset adapter when reproducing results.

| Operator | Input → output |
|---|---|
| `McapInputOperator` | Source MCAP and adapter → aligned numeric arrays and original frame times |
| `GlobalStatsOperator` | Selected MCAP episodes → Stage1/3 global statistics and alignment lag |
| `GripperGapOperator` | Source MCAP gripper timestamps and quality frame times → invalid-frame mask and left/right gap intervals |
| `Stage1Operator` | Arrays and statistics → sudden-change result |
| `Stage2Operator` | Arrays and lag → direction-agreement result |
| `Stage3Operator` | Arrays and statistics → extreme-value result |
| `Stage4Operator` | Arrays and valid-prefix end → long-static-run removals |
| `FinalDecisionOperator` | All four results → frame mask, episode decision and reasons |
| `McapExportOperator` | Source MCAP and frame decision → cleaned MCAP and audit JSON |

`operators.yaml` is the platform registration inventory. `QualityEpisode` and
`CleaningDecision` contracts are in `stage_b_mcap_v3/contracts.py`.

## First dataset profile

`configs/abc_130k.yaml` uses the existing ABC-130K MCAP adapter, its
`quality_stage_b.yaml`, and the original package's discovery logic. **Its raw
MCAP alone does not contain usable EEF poses**; the input tree must also have
the official `eef_pose.parquet` beside each `episode.mcap`. Each output is a
cleaned `episode.mcap`; the source EEF parquet is read for quality judgement but
is not copied into the output MCAP. Other MCAP topic layouts need their own
adapter, quality YAML and source profile.

## Jindian Marvin MCAP test profile

`configs/jindian_marvin.yaml` and `dataset_adapters/jindian_marvin/` read the
delivered `jindian_test-简智夹爪` MCAP directory directly. No Stage A files are
needed. The adapter decodes ROS2 CDR joint-state and joint-target messages,
aligns both arms and gripper widths, and samples the 50 Hz robot stream at
25 Hz. It keeps all seven joints in each arm. The provisional quality canvas
is 16d: left arm 7 + right arm 7 + left/right gripper widths. Stage2 maps the
two 7-joint arms explicitly. TCP pose and video remain in the MCAP but are not
used in the current Jindian quality decision. The copied Stage B thresholds
are a technical test profile, not calibrated quality criteria for this robot.

```bash
python3 run.py quality --config configs/jindian_marvin.yaml \
  --repo-root /path/to/SLM_data_curation \
  --input-root '/Users/flashryk/Desktop/jindian_test-简智夹爪' \
  --output-root /path/to/jindian_quality_report

# After reviewing the quality report, export selected episodes separately:
python3 run.py run --config configs/jindian_marvin.yaml \
  --repo-root /path/to/SLM_data_curation \
  --input-root '/Users/flashryk/Desktop/jindian_test-简智夹爪' \
  --output-root /path/to/jindian_cleaned_mcap
```

The optional `source.gripper_gap` rule in `configs/jindian_marvin.yaml` sets
the maximum distance to a measured left/right gripper state at 0.6 seconds.
`GripperGapOperator` marks frames beyond that limit invalid and records each
side's intervals in `gripper_gap_ranges` in `decisions.jsonl` and the MCAP audit.
Stage1–4 run separately on the contiguous valid sections. A Stage2 or Stage3
episode-level rejection then applies only to its section; sections too short
for the configured Stage B minimum are rejected. The final MCAP keeps all
accepted sections on their original source timestamps. The source MCAP is not
modified. This rule detects missing measurements; it does not infer gripper
positions inside a gap. Other dataset profiles leave the operator disabled.
The earlier 88-episode baseline and current gap-operator checks are recorded in
`JINDIAN_TEST_20261027.md`.

The adapter also accepts MCAP files placed directly under the input root, as
well as one directory below it. A 100-episode flat-layout batch completed
quality judgement without input failures; see `docs/SHANGPIN_TEST_20261027.md`.

## Run

Install the upstream engine, this package and FFmpeg (including libx264; add
libx265 for H.265 input). Python dependencies include `mcap`,
`mcap-protobuf-support`, NumPy, PyArrow, SciPy, PyYAML and tqdm.

```bash
python3 -m pip install -e /path/to/SLM_data_curation/qwen-manip-preprocess
python3 -m pip install -e /path/to/stage_b_operators_v3

python3 run.py stats --config configs/abc_130k.yaml \
  --repo-root /path/to/SLM_data_curation \
  --package-dir /path/to/SLM_data_curation/datasets/abc_130k \
  --quality-yaml /path/to/SLM_data_curation/datasets/abc_130k/quality_stage_b.yaml \
  --input-root /path/to/ABC-130k-EEF --output-root /path/to/v3-output

python3 run.py quality --config configs/abc_130k.yaml \
  --input-root /path/to/ABC-130k-EEF --output-root /path/to/v3-output \
  --sample-size 10

python3 run.py run --config configs/abc_130k.yaml \
  --input-root /path/to/ABC-130k-EEF --output-root /path/to/v3-output \
  --sample-size 10

# A single-file smoke test avoids scanning the whole dataset tree.
python3 run.py quality --config configs/abc_130k.yaml \
  --input-root /path/to/ABC-130k-EEF --output-root /path/to/v3-smoke \
  --mcap /path/to/ABC-130k-EEF/data/train/task/episode_id/episode.mcap
```

When the code is deployed to another machine, pass the three repo/package/
quality paths above there as well. `stats` writes cached global statistics;
`quality` writes decisions without MCAP export; `run` writes both. Use
`--recompute-stats` for a manual refresh; input file sizes, modification times,
episode selection, quality YAML, adapter code and gripper-gap configuration are
fingerprinted to invalidate the cache automatically. `--overwrite`
allows replacing a prior v3 output, never the source MCAP.

## Removal and output semantics

Stage1/3 truncate from the first abnormal quality frame to the end; Stage2
can discard the whole episode; Stage4 removes excess frames in long static
runs within the valid prefix. The exporter assigns each original MCAP message
to the nearest quality frame. Calibration and instruction topics listed in the
source profile are retained. Source timestamps and non-video message payloads
are preserved in the new file. A discarded episode produces only an audit JSON.

An episode with only a trailing cut copies its original compressed video
messages. Interior cuts in H.264/H.265 trigger a full re-encode of the retained
video frames, with independently decodable output. This simple, correct first
version uses temporary elementary video streams per affected episode. It needs
space for those temporary streams while exporting; it does not create a Stage A
Parquet/video copy.

The source MCAP is never edited. v3 writes an episode audit JSON, a
`decisions.jsonl` report and `run_summary.json` beside its output tree.

## Call Stage1–4 separately

```python
from pathlib import Path
from stage_b_mcap_v3 import (
    McapInputOperator, GlobalStatsOperator,
    Stage1Operator, Stage2Operator, Stage3Operator, Stage4Operator,
    GripperGapOperator, FinalDecisionOperator, McapExportOperator, build_prefix,
    judge_one,
)
from stage_b_mcap_v3.spec import load_spec, quality_config, discover, build_adapter

spec = load_spec(Path("configs/abc_130k.yaml"))
cfg = quality_config(spec)
refs = discover(spec, limit=10)
stats = GlobalStatsOperator().run(spec, cfg, refs)
episode = McapInputOperator().run(refs[0], spec, cfg, build_adapter(spec))

s1 = Stage1Operator().run(episode, cfg, stats)
s2 = Stage2Operator().run(episode, cfg, stats)
s3 = Stage3Operator().run(episode, cfg, stats)
prefix = build_prefix(episode.num_frames, s1, s2, s3, cfg)
s4 = Stage4Operator().run(episode, prefix.valid_end, cfg)
gaps = GripperGapOperator().run(episode, spec)
# This composes Stage1–4 separately on each valid section when gaps exist.
decision = judge_one(episode, cfg, stats, gaps)
McapExportOperator().run(decision, spec)
```

For an isolated Stage4 check, pass `episode.num_frames` as `valid_end`.

## Verification performed on 2026-10-26

- Three local tests passed: ABC MCAP input and 32d quality-space mapping,
  global statistics and Stage1–4 composition, non-video MCAP rewriting, and
  H.264 interior-cut rewriting with video decode.
- One real ABC-130K MCAP with 6,634 quality frames completed `quality` mode.
  Its first quality frame was Stage3-abnormal, so the actual Stage B decision
  discarded that episode; no cleaned MCAP was produced from that decision.
- The same source was separately given **manual test masks** to exercise
  export: a 60-frame prefix and a 60-frame selection with an interior gap.
  Both new MCAPs were readable and their reference-camera video decoded.
  The interior test re-encoded all three compressed video channels. These
  manual masks are export checks, not claims that those frames passed Stage B.
- A complete ABC-130K run has not been performed.
