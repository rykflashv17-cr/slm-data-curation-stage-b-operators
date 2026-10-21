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

在独立 Python 环境中安装 `container/requirements-runtime.txt` 的依赖；
运行下面的测试时再安装 `pytest`。
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

`quality` 使用原仓库 Stage B 的 report 模式，不写筛选后视频；
P3 报告位于 `output/_quality/reports/`。`run` 依据所选包的配置写
质量报告及 LeRobot 文件。原仓库的人工审核门禁仍生效；
`--force-skip-gate` 仅用于实验。结果应检查 `run_meta.json`、
`_quality/reports/quality_report.json`、
`_quality/reports/exclusion_log.jsonl`，以及导出模式下的
`data/`、`videos/` 和 `meta/`。

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

`tests/test_stage_b_configs.py` 会检查 12 个配置及适配器接口，
`tests/test_parity.py` 现有的 Episode 级数值对照覆盖 EgoStandard。
前者要求明确设置完整上游路径，避免误用旧单包快照：

```bash
export SLM_DATA_CURATION_ROOT=/path/to/full/SLM_data_curation
export PYTHONPATH="$(dirname "$PWD"):$SLM_DATA_CURATION_ROOT/qwen-manip-preprocess/src"
python3 -m pytest -q tests/test_stage_b_configs.py tests/test_parity.py
```

这些测试不等于 12 个数据集的端到端验收。正式覆盖还需逐包用真实
Stage A 小样本对照原管线，核验保留帧、剔除原因、Parquet 字段与值、
视频帧数和元数据；在此之前应将该实现视为多数据集适配中的版本。
