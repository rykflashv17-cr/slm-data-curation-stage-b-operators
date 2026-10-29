# Stage B v4：整条 MCAP 质量筛选

v4 只有三个注册算子：**MCAP读取、全局统计、质量检测**。质量检测合并了夹爪缺测和 S1–S4；任何一项产生不合格帧、待删除片段或整段丢弃判定，就拒绝整个源 MCAP。

`quality` 仅生成报告；`run` 在整批判定持久保存后，删除被拒绝的源 MCAP。通过的 MCAP 完整保留在原位置。v4 不切视频、不重编码、不输出新的 MCAP，也没有独立的最终判定或 MCAP 导出算子。v3 保留在相邻目录，不被 v4 覆盖。

| 编号 / 注册 ID | 名称 | 输入 | 输出 |
|---|---|---|---|
| B01 / `mcap_read` | MCAP读取 | 一条 MCAP 的引用、适配器、配置 | 对齐的状态、动作、原始帧时间戳与启动排除掩码 |
| B02 / `global_stats` | 全局统计 | 本批可读取且通过夹爪覆盖预检的 MCAP、质量配置 | S1 阈值、S3 分位数、状态与动作时滞及缓存 |
| B03 / `quality_detection` | 质量检测 | 一条已读取数据、统计结果、配置 | 整条文件通过 / 拒绝、触发规则、指标、审计时间段 |

实际调度先读取每条文件，并调用质量检测内部的夹爪覆盖预检；预检已拒绝的文件直接记录拒绝及 S1–S4 跳过。其余可读取文件才参与全局统计，然后逐条执行完整 S1–S4。整批都因夹爪缺测被拒绝时，不需要拟合统计也可完成判定和 `run` 删除。

当前全局统计会自己再次读取这些 MCAP，不会直接复用 B01 的全部内存对象。B03 的数值检测同时需要 B01 的单条数值数据和 B02 的统计基准；夹爪预检本身不需要统计。`operators.yaml` 是注册清单；详细字段见 [docs/OPERATOR_IO.md](docs/OPERATOR_IO.md)。

## 使用范围

| Profile | 所需数据 | 判定数据结构 |
|---|---|---|
| `configs/jindian_marvin.yaml` | 简智夹爪 / Marvin 的原始 ROS2 CDR MCAP；支持输入根目录和下一层目录中的 `*.mcap` | 16 维：左右各 7 个关节 + 左右夹爪宽度；50 Hz 状态每隔一帧取样，约 25 Hz |
| `configs/abc_130k.yaml` | ABC-130K-EEF 目录；每条 `episode.mcap` 旁必须有官方 `eef_pose.parquet` | 32 维 joint / gripper / EEF 质量数据；约 30 Hz |

简智夹爪默认开启夹爪缺测检测：每个质量帧到左右夹爪实际读数的最近时间距离不能超过 0.6 秒；任一侧超限或没有读数即拒绝整个 MCAP。ABC profile 当前没有开启夹爪缺测检测。简智夹爪的阈值沿用 v3 的技术测试配置，尚未标定成通用机器人质量标准。

若简智夹爪某侧完全没有读数，数组中该侧保留 `NaN` 表示未知，由覆盖预检拒绝整条；不会把未知读数当成有效零值。若关闭夹爪覆盖检查仍存在这类未知值，数值阶段不能正常判定，文件记为错误并保留。

当前数值判定不使用视频像素做模糊、曝光或遮挡检测。原始 UMI、Ego 或其他 MCAP topic 布局需要新增对应适配器和质量配置，不能仅更换输入路径。ABC 的 parquet 只用于读取质量数据；删除 MCAP 时不会连带删除 parquet 或父目录。

上游 Stage B 引擎接口基于提交 `0e694dbc9706b2a5bac8e550f8cd9de18f5b7473`。本地默认指向 `基模/github/SLM_data_curation`；换机器时用命令行指定引擎和数据包路径。

## 安装与配置

```bash
python3 -m pip install -e /path/to/SLM_data_curation/qwen-manip-preprocess
python3 -m pip install -e /path/to/stage_b_operators_v4
```

两个示例 YAML 的输入路径均为占位符 `/path/to/input-mcaps`，必须用 `--input-root` 指定实际目录。默认输出是 `ryk/slm_data_curation/stage_b_v4_outputs/` 下的报告和缓存目录；不会向 `Processed` 写入结果。也可用 `--output-root` 指定另一个报告目录。

```bash
# 1. 可选：直接对选中批次计算统计，不做夹爪预检或删除。
python3 run.py stats --config configs/jindian_marvin.yaml \
  --input-root /path/to/input-mcaps \
  --output-root /path/to/ryk/v4-quality-report

# 2. 检测并保存报告，不做删除。
python3 run.py quality --config configs/jindian_marvin.yaml \
  --input-root /path/to/input-mcaps \
  --output-root /path/to/ryk/v4-quality-report

# 3. 检测、保存整批判定，然后删除被拒绝的源 MCAP。
python3 run.py run --config configs/jindian_marvin.yaml \
  --input-root /path/to/input-mcaps \
  --output-root /path/to/ryk/v4-run-report
```

上述 `run` 会实际删除输入目录中的不合格 MCAP；它不只是生成待删除清单。只想查看结果时用 `quality`。

`quality` 和 `run` 不能覆盖已有审计报告，请为每次检测指定新的 `--output-root`。只有缓存、尚无报告的统计目录可继续用于一次 `quality` 或 `run`；上面前两条命令因此可以共用目录，第三条使用新目录。

可重复使用 `--mcap` 限定本次批次。以下仅检测两个文件，并按其中可读取且通过夹爪覆盖预检的文件计算统计，不会自动使用整个目录计算统计：

```bash
python3 run.py quality --config configs/jindian_marvin.yaml \
  --input-root /path/to/input-mcaps \
  --output-root /path/to/ryk/v4-selected-report \
  --mcap /path/to/input-mcaps/episode_a.mcap \
  --mcap /path/to/input-mcaps/episode_b.mcap \
  --recompute-stats
```

ABC 命令将 `--config` 换成 `configs/abc_130k.yaml`；可通过 `--repo-root`、`--package-dir`、`--quality-yaml` 覆盖上游引擎、适配器和质量 YAML 的位置。

`quality` / `run` 的全局统计针对**本批可读取且通过夹爪覆盖预检的文件**；输入错误和已因夹爪缺测拒绝的文件不参与统计。缓存会根据文件和配置指纹校验。单个 MCAP 也能作为一个批次计算，但统计基准更容易被它自身影响。`--recompute-stats` 强制重算。

独立 `stats` 命令直接对所选引用调用统计算子，不走 `quality` / `run` 的覆盖预检，也不生成逐条判定报告。非有限数组会由统计读取钩子拒绝，后续由上游统计逻辑跳过或报错；它不应作为查看夹爪缺测结果的替代命令。通常直接运行 `quality` 即可自动完成所需统计。

读取、对齐或数值判定失败的文件记为 `error` 并保留。本批需要的全局统计失败时，依赖该统计的文件记为错误，整批删除被禁用；先前夹爪预检的拒绝判定仍记录，但也不会在本次失败运行中删除。全批均因夹爪缺测拒绝、根本无需拟合统计的情况不属于统计失败。错误不等于质量不合格，不能因此自动删除源文件。

## v4 的整条淘汰规则

- 夹爪缺测命中：立即拒绝整个 MCAP；不在缺口两侧挽救片段。
- S1 异常帧或整段丢弃命中：拒绝整个 MCAP。
- S2 整段丢弃命中：拒绝整个 MCAP。
- S3 待删除帧或整段丢弃命中：拒绝整个 MCAP。
- S4 产生待删除帧：拒绝整个 MCAP。

通过夹爪覆盖预检后，S1–S4 均在原始完整 Episode 上计算；不先截取 S1/S3 有效前缀再跑 S4，也不加入独立的最短前缀或重复尾段判定。S3 自带的 `min_episode_length` 丢弃检查仍按其原配置执行。

启动零值与宽限期等配置定义的排除掩码仍然有效；在排除范围内跳过的检测不是“命中不合格”。S1–S4 的具体阈值继续来自对应数据集的 `quality_stage_b.yaml`，v4 改变算子边界和整文件保留策略，并未重新标定阈值。

例如原文件 10 秒，仅第 2–3 秒被判为不合格，v4 的结果是整条拒绝。`quality` 会记录这一结果；`run` 会删除原 MCAP。输入 10 条，最后通过并留在输入目录中的文件数为 0–10 条，不会切成 18 条。

删除操作只作用于本批已完成质量拒绝判定的源 MCAP。整批判定先写入报告，随后逐条记录删除结果；输入错误和判定后发生变化的文件会保留并记录原因。符号链接及含符号链接的父路径可被读取判定，但该文件删除会跳过并审计，不阻止其他合格删除任务。开发验证只在临时复制或合成的 MCAP 上使用 `run`，真实数据使用 `quality` 检查。

## 输出

| 输出项 | 内容 |
|---|---|
| `decisions.jsonl` | 每条 MCAP 的保留 / 拒绝 / 输入错误、原因、检测区间和各阶段指标 |
| `deletion_plan.json` | 本批拒绝文件及删除资格；在任何源文件删除前保存 |
| `deletion_events.jsonl` | `run` 的删除尝试与结果；`quality` 不执行删除 |
| `run_summary.json` | 保留、拒绝、错误、实际删除数量与运行耗时等汇总 |
| `cache/` | 全局统计与源数据缓存 |

审计时间段用于说明哪项规则触发整条拒绝，不再代表应导出哪几个片段。保留文件仍在输入目录中，输出目录不包含清洗后的 MCAP。

v4 的三个类是进程内 Python 接口；平台注册仍需把内存对象接入平台数据端口。这里提供的 YAML 是注册清单和运行配置，不是已经部署的平台注册服务。

## 验证

32 项测试通过，覆盖两个格式的临时 MCAP 集成、任一坏帧整条拒绝、NaN 缺失语义、无统计的全缺测批次、统计失败整批保留、输入错误保留、删除身份检查和审计失败停止。测试只使用临时合成数据，没有删除真实样本。命令和覆盖范围见 [docs/VALIDATION.md](docs/VALIDATION.md)。
