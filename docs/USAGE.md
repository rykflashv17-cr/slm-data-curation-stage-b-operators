# 多数据集 Stage B 算子使用说明

![Stage B v2 的 16 个算子及数据流](stage_b_operator_io.png)

## 范围

本目录封装 `StridingAI/SLM_data_curation` 的 Stage B 质量处理：
`pseudo_v3` 输入，经全局统计、Stage1–4、最终判定、分片导出与归并。
通用执行器调用原仓库的数据集适配器和 P0/P7 实现，因此需要**完整的
上游仓库 checkout**。它不执行 Stage A 的原始数据读取、几何对齐和
`raw → pseudo_v3` 转换。

当前上游有 12 个显式 `run_config_stage_b.yaml` 数据集包，包括
EgoStandard、EgoVerse、EgoDex、Humanoid、ABC130K、AgiBotWorld、
RoboMind v2 的两个 H5 包和 InternDataA1 的四个本体。另有
`robomind_ur`、`RC_Table30v2/ARX5`、`RC_Table30v2/UR5` 等入口，
目前没有专门的 Stage B 配置；不能仅凭本工具就宣称它们已有 Stage B
端到端验证。

12 个包的输出格式遵循**各自的上游 YAML**：6 个为 `pseudo_v3`，
6 个为 `aggregated_v3`。`--output-layout` 只在明确需要覆盖配置时使用；
不要假设每个数据集都应该强制聚合。

## 16 个算子的输入、输出与使用范围

下表对应 [`operators.yaml`](../operators.yaml) 中的类。数据集级算子处理一批
episode；逐条算子只处理一条 episode 的质量数组。默认 `run` 调用原仓库
P0/P2/P7 编排，其中门禁、发现、标准化读入和同步导出由原仓库执行；
同名包装类可单独调用。P3 的质量算子由本项目实际组合执行。

| 算子 | 输入 → 输出 | 使用位置 |
|---|---|---|
| `ConfigOperator` | 仓库与包名/YAML → `StageBDatasetSpec` | 选择数据集、阈值与输出布局 |
| `GateOperator` | `StageBDatasetSpec` + `review_checklist.yaml` → `GateDecision` | 人工审核门禁 |
| `DiscoverOperator` | Stage A 根目录 + 采样/分片参数 → `DiscoveryResult[refs, stats_refs]` | 选 episode；保留全局统计集合 |
| `StatisticsOperator` | `PipelineConfig` + 全部选中 episode → `StatisticsBundle` | 全局 Stage1/3 阈值和 lag |
| `InputOperator` | 一条 Stage A Parquet + adapter → `QualityEpisode` | 转到本数据集质量空间 |
| `Stage1Operator` | `QualityEpisode` + 全局阈值 → `Stage1Result` | 突变与故障检测 |
| `Stage2Operator` | `QualityEpisode` + lag → `Stage2Result` | 状态与动作方向一致性 |
| `Stage3Operator` | `QualityEpisode` + 分位数 → `Stage3Result` | 极值检测 |
| `PrefixOperator` | Stage1–3 结果 → `PrefixDecision` | 计算首个异常帧与有效前缀 |
| `Stage4Operator` | 有效前缀中的 `QualityEpisode` → `StaticDecision` | 缩短静止段 |
| `FinalDecisionOperator` | 各阶段结果 → `EpisodeResult` | 丢弃原因与逐帧保留掩码 |
| `QualityOperator` | 一条 episode + 统计 → `EpisodeResult` | 组合上述七个逐条算子 |
| `AlignedEpisodeOperator` | `EpisodeRef` + adapter → `AlignedEpisode[StandardEpisode, arrays]` | Stage B identity P1 读入 |
| `ReportOperator` | 多个 `EpisodeResult` + 统计摘要 → 3 个 JSON/JSONL 报告 | 质量审计 |
| `ExportOperator` | 包配置 + Stage A 集合 → Parquet、视频、元数据分片 | 调原仓库 P0–P7，并注入 v2 P3 |
| `AggregateOperator` | 多个分片 + 包配置 → 最终数据集、相对统计 | 调该包 `merge_shards.py` |

常用调用方式是下文的 `list`、`quality`、`run`、`merge` 命令。
如果要在 Python 中单独检查数据集与样本选择，可直接使用前几个类：

```python
from pathlib import Path
from stage_b_operators.operators import ConfigOperator, GateOperator, DiscoverOperator

spec = ConfigOperator().run(Path("/path/to/SLM_data_curation"), "Egostandard")
gate = GateOperator().run(spec, output_mode="report")
selection = DiscoverOperator().run(
    spec, input_root=Path("/path/to/stage_a_aligned"), sample_size=10
)
print(spec.quality_config_path, spec.output_layout, len(selection.refs))
```

逐条质量算子使用 `robot_data_processing.pipeline.PipelineConfig` 和
`StatisticsBundle`。需要先安装本数据集的 Stage B adapter hook 并计算
全局统计；直接集成时可参考 [`pipeline.py`](../pipeline.py)。
单独调用 `Stage1Operator` 等不会自动执行后续裁决或导出。

## 本机安装和检查

```bash
export SLM_DATA_CURATION_ROOT=/path/to/full/SLM_data_curation
cd /path/to/stage_b_operators_v2
python3 run.py --repo-root "$SLM_DATA_CURATION_ROOT" list
```

`--repo-root` 也可以改为环境变量 `SLM_DATA_CURATION_ROOT`。请核对
该路径下有 `datasets/`、`qwen-manip-preprocess/src/` 和 12 个
`run_config_stage_b.yaml`。工作区旧的相邻 `upstream/SLM_data_curation`
快照只有 EgoStandard，不适合作为 v2 的上游输入。

在独立 Python 环境中安装运行依赖、原仓库引擎和本算子包；
运行下面的测试时再安装 `pytest`：

```bash
python3 -m pip install -r container/requirements-runtime.txt
python3 -m pip install -e "$SLM_DATA_CURATION_ROOT/qwen-manip-preprocess"
python3 -m pip install --no-deps -e .
```

安装后 `slm-stage-b` 与 `python3 run.py` 是同一个命令入口。
可安装包名固定为 `stage_b_operators`，即使 checkout 目录名带
`_v2`，上文的 Python 导入示例也会指向 v2。
Stage B 虽然读取对齐后的 `pseudo_v3`，仍会加载数据集适配器；
H5 适配器需要 `h5py`，ABC130K 的 MCAP 工具可能需要 `mcap`、
`mcap-protobuf-support`。导出视频还需 FFmpeg/ffprobe 和 libx264。
本目录的容器构建配方见 [`../container/README.md`](../container/README.md)。

## 运行一个数据集

先用 `quality` 对少量 Stage A episode 生成报告，再用 `run` 导出。
下面示例的 `/path/to/stage_a_aligned` 必须对应 `Egostandard` 包；
命令里的 `--input-root` 会覆盖上游 YAML 中的历史绝对路径。

```bash
python3 run.py --repo-root "$SLM_DATA_CURATION_ROOT" quality \
  --dataset Egostandard \
  --input-root /path/to/stage_a_aligned \
  --output-dir /path/to/output/quality \
  --sample-size 8 --workers 4
```

```bash
python3 run.py --repo-root "$SLM_DATA_CURATION_ROOT" run \
  --dataset Egostandard \
  --input-root /path/to/stage_a_aligned \
  --output-dir /path/to/output/clean \
  --sample-size 8 --workers 4 --export-workers 4
```

`quality` 只运行 P3 质量检测，直接在 `output/reports/` 写
`quality_report.json`、`exclusion_log.jsonl` 和
`optimal_lags.json`；它不检查输入视频，也不写筛选后视频。
`run` 执行原仓库的完整 Stage B 编排，报告在
`output/_quality/reports/`，另有 `run_meta.json`、`data/`、
`videos/` 和 `meta/`。两者都执行人工审核门禁；
`--force-skip-gate` 仅用于实验。

可用 `--dataset InternDataA1_sim/franka` 选择嵌套包，也可传
`--package-dir` 或 `--config`。这三种方式都应指向**同一个**上游包；
`list` 命令用于查看自动发现的包。

## 分片作业

全局统计要在分片处理前完成一次，然后各分片共用同一统计缓存。
以下是调用结构，实际输出路径按作业环境设置：

```bash
python3 run.py --repo-root "$SLM_DATA_CURATION_ROOT" run \
  --dataset Egostandard --input-root /path/to/stage_a_aligned \
  --output-dir /path/to/output/stats --stats-cache-dir /path/to/output/stats_cache \
  --stats-only

python3 run.py --repo-root "$SLM_DATA_CURATION_ROOT" run \
  --dataset Egostandard --input-root /path/to/stage_a_aligned \
  --output-dir /path/to/output/shard_0 --stats-cache-dir /path/to/output/stats_cache \
  --shard-id 0 --num-shards 2 --export-shard-only

python3 run.py --repo-root "$SLM_DATA_CURATION_ROOT" run \
  --dataset Egostandard --input-root /path/to/stage_a_aligned \
  --output-dir /path/to/output/shard_1 --stats-cache-dir /path/to/output/stats_cache \
  --shard-id 1 --num-shards 2 --export-shard-only

python3 run.py --repo-root "$SLM_DATA_CURATION_ROOT" merge \
  --dataset Egostandard \
  --shard-dir /path/to/output/shard_0 \
  --shard-dir /path/to/output/shard_1 \
  --output-dir /path/to/output/merged
```

多机统计还提供 `resolution_census`、`resolution_plan`、`pass1`、
`prepare_pass2`、`pass2`、`finalize` 六阶段。前两个是按配置启用的
分辨率预处理阶段，当前 12 个默认 Stage B 配置未启用；实际分布式
运行前须按上游配置和数据集环境分别验证。

## 测试边界

`tests/test_stage_b_configs.py` 会检查 12 个配置及适配器接口；
`tests/test_generic_quality.py` 测试真实 `pseudo_v3` 布局的质量读取、
RoboMIND H5 分布式统计和 `quality` 命令；
`tests/test_parity.py` 的 Episode 级数值对照覆盖 EgoStandard。
前者要求明确设置完整上游路径，避免误用旧单包快照：

```bash
export SLM_DATA_CURATION_ROOT=/path/to/full/SLM_data_curation
python3 -m pytest -q tests
```

这些测试不等于 12 个数据集的端到端验收。正式覆盖还需逐包用真实
Stage A 小样本对照原管线，核验保留帧、剔除原因、Parquet 字段与值、
视频帧数和元数据；在此之前应将该实现视为多数据集适配中的版本。
