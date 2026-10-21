# SLM Data Curation Stage B operators · v2

v2 面向 [StridingAI/SLM_data_curation](https://github.com/StridingAI/SLM_data_curation)
的**整个 Stage B**，以该仓库 `48e1595` 的 12 个
`datasets/**/run_config_stage_b.yaml` 为对照。v1 仅针对 EgoStandard，已归档为
[`v1.0.0`](https://github.com/rykflashv17-cr/slm-data-curation-stage-b-operators/tree/v1.0.0)。
这里的 Stage1–4 指原仓库的质量规则，不是之前另一个 EgoStandard 清洗管线的 S1–S4。

## 使用范围

- 输入是 **Stage A 已对齐的 `pseudo_v3` 单根数据集**，质量空间与阈值由各数据集包的
  adapter 和 `quality.yaml` / `quality_stage_b.yaml` 决定。
- 支持原仓库目前全部 12 个 Stage B 包：`Egostandard`、`Egoverse`、
  `egodex_lerobot_v21`、`abc_130k`、`AgibotWorld_Beta`、`humanoid_merged`、
  `Robomindv2/agilex_h5`、`Robomindv2/ur_h5`、`InternDataA1_sim/` 下的
  `franka`、`genie1`、`lift2`、`split_aloha`。
- 输出布局遵循各包原配置：6 个 `pseudo_v3`、6 个 `aggregated_v3`。
  质量统计必须先覆盖全部选中 episode，然后才能分片筛选和导出。
- 不运行 Stage A 的原始数据接入、几何修复或对齐工作。没有 Stage B 配置的入口不在
  此版本范围内。开启 Stage5 或分辨率统一的包继续使用原仓库相应流程。

## 算子边界

`operators.yaml` 是机器可读的输入输出清单。核心路径为：

| 顺序 | 算子 | 输入 → 输出 |
|---|---|---|
| 1 | 配置解析、人工审核门禁、episode 发现 | 仓库与 Stage B 配置 → 包规格、审核结果、`EpisodeRef` |
| 2 | 全局统计 | 所有选中 episode → Stage1 阈值、Stage3 分位数、lag 缓存 |
| 3 | 质量读取与转换 | Stage A Parquet + 数据集 adapter → `QualityEpisode` |
| 4 | Stage1、Stage2、Stage3 | `QualityEpisode` + 统计 → 三组检测结果 |
| 5 | 前缀裁决、Stage4、最终裁决 | 检测结果 + 质量数据 → `EpisodeResult` 和逐帧保留掩码 |
| 6 | 对齐读取、报告、分片导出 | 原 episode + 掩码 → 报告、Parquet、视频、元数据 |
| 7 | 数据集归并 | 分片 → 重编号后的数据集、相对统计 |

这些边界对应 16 个可调用类；其中 `QualityOperator` 把 Stage1–4 等类组合成逐条质量计算。
视频、Parquet 和元数据的写出仍由原仓库 P7 在同一轮中同步执行，保证帧索引一致；
`ExportOperator` 负责把 v2 质量结果接入原仓库编排。审核、发现、对齐读取也保留原仓库
编排执行，并提供独立包装类供单独调用。请参阅 [完整算子表与用法](docs/USAGE.md)。

## 快速使用

需要 Python 3.10+、原仓库依赖，以及导出视频时的 FFmpeg/ffprobe。
将 `--repo-root` 指向**完整**的原仓库检出；当前同级旧 `upstream/` 快照只含
EgoStandard，不适合 v2 的全数据集运行。

```bash
python run.py --repo-root /path/to/SLM_data_curation list
```

```bash
python run.py --repo-root /path/to/SLM_data_curation quality \
  --dataset InternDataA1_sim/franka \
  --input-root /path/to/stage_a_aligned \
  --output-dir /path/to/stage_b_report --sample-size 10 --workers 4
```

```bash
python run.py --repo-root /path/to/SLM_data_curation run \
  --dataset InternDataA1_sim/franka \
  --input-root /path/to/stage_a_aligned \
  --output-dir /path/to/stage_b_shard \
  --sample-size 10 --workers 4 --export-workers 2
```

`quality` 强制只生成报告，不写筛选后的视频；`run` 使用该包 Stage B 配置的
`output_mode` 和输出布局，分片归并需单独执行。全量分片、共享统计和归并命令见
[使用说明](docs/USAGE.md)。Docker 构建输入与限制见 [容器说明](container/README.md)。

## 版本与验证

v1 的已发布提交为 `32302b7`；v2 从其标签分出 `v2` 分支，保留原本本地
`benchmarks/` 数据。2026-10-21 在上游 `48e1595` 上运行 40 项测试通过：
覆盖 12 包配置与 adapter、EgoStandard 逐帧对照、RoboMIND H5 分布式统计、
以及 `quality` 命令。尚未逐包用真实 Stage A 视频验收导出和两种布局的
分片归并；v1 的 EgoStandard 容器实测不能代替 v2 的验收。
