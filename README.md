# Stage B v4：四个算子严格串行

完整检测流程固定为 **B01 格式统一 → B02 全局统计 → B03 质检判断 → B04 结果交付**。全批完成前一阶段，才进入下一阶段；每条源 MCAP 仅在 B01 通过适配器解码数值数据一次，B02/B03 使用已保存的标准数据；B04 读取源文件字节进行复制，不重新解码或质检。B01 在适配器前另做一次完整文件结构与可用 CRC 校验，增加一次文件读取及压缩块解压开销。

| 编号 / 注册 ID | 职责 | 输入 → 输出 |
|---|---|---|
| B01 / `mcap_read` | 文件完整性校验、字段转换、对齐、真实时间轴、输入错误记录 | 源文件引用列表 → `NormalizedBatchRef`，包含完整清单及 NPZ 数据 |
| B02 / `global_stats` | 选择可用于统计的数据，拟合 S1 / S3 / 时滞统计 | `NormalizedBatchRef` → `StatisticsContext`，携带原完整清单与统计结果 |
| B03 / `quality_detection` | 缺测及 S1–S4 判断、保存判定 | `StatisticsContext` → `BatchQualityResult` 与可重载的 `quality_batch.json` |
| B04 / `mcap_delivery` | 复制合格 MCAP、执行可选拒绝处置 | `BatchQualityResult` → `accepted/`、交付清单与 `BatchDeliveryResult` |

B01 不生成启动零值或 S1 宽限期掩码，这些质量规则在 B03 内计算。B02 只记录统计纳入 / 排除及原因，不输出文件通过 / 拒绝判定。输入错误、覆盖不足或数值不完整的文件不参与拟合，但仍原样传到 B03；最终每条都有结果。全批没有统计候选时，B02 返回 `empty`，B03 继续处理，不能绕过第二个算子。

`quality` 运行完整 1→2→3→4，将合格 MCAP 原样复制到独立的 `accepted/`，保留全部源文件。`run` 在交付与审计保存成功后，进一步删除明确拒绝的源 MCAP。`stats` 是 1→2 的调试命令。B03 不再复制或删除源文件。

B04 是 PM 图中“输出合格 MCAP / 删除不合格 episode”两个分支的执行节点，只执行判定，不改变质量结论。输出保留源相对目录结构，避免不同目录中的 `episode.mcap` 同名冲突；文件逐字节复制，不切片、不重编码、不改 MCAP topic/schema。`accepted/` 内只有 MCAP，标准中间文件与报告放在外面。

B03 的正常诊断策略不变：缺测命中时停止当前条后续检查；进入 S1–S4 后，质量不合格仍继续收集后续诊断。只有执行报错时才停止余下数值阶段，并保存此前已确定的拒绝和证据。因此不声称 PM 图中“检测到不合格直接终止”的逐项质量短路已实现。

接口见 [OPERATOR_IO.md](docs/OPERATOR_IO.md)，本次四项复查修复和验证见 [REVIEW_FIX_HANDOFF.md](docs/REVIEW_FIX_HANDOFF.md)。

## 输入及判定范围

B01 对所有 topic 所在的记录、压缩块、附件和文件尾进行结构与可用 CRC 校验，包括无索引文件和只含视频消息的块。发现损坏记为输入错误，不参与统计、不输出为合格文件、不删除源文件。MCAP 合法允许省略的 CRC（值为 0）不强制补齐；没有 CRC 的内容无法据此证明未被修改。视频负载只做容器完整性校验，不做像素质量判断。

| Profile | 输入 | 标准数据 |
|---|---|---|
| `configs/jindian_marvin.yaml` | 输入根目录或下一层目录中的 Marvin ROS2 CDR MCAP | 16 维：左右各 7 关节和左右夹爪宽度；50 Hz 状态隔帧采样，约 25 Hz |
| `configs/abc_130k.yaml` | ABC 官方 MCAP 布局及配套 `eef_pose.parquet` | 32 维 joint / gripper / EEF，约 30 Hz；外部适配器待联调 |

保留两个 P0 修复：空夹爪消息不再充当有效测量；状态过滤后的时间轴随实际质量行输出，不按消息数或固定步长推算。适配器必须实现 `to_quality_timeline(standard)`，返回真实帧时刻及有效测量时刻。本地 Jindian 已接入；外部 ABC 适配器仍需同步接入，缺失接口按输入错误保留文件。

Jindian 默认检查每个质量帧到左右夹爪最近有效读数的距离：严格大于 0.6 秒或整侧无读数时，B03 拒绝整条文件。前后的有效读数均参与距离计算。有效读数须包含正确宽度字段且有限，空消息、错字段、NaN/Inf 不刷新读数时间。完全缺失的一侧保留 NaN 表示未知；关闭覆盖检测仍有未知数值时，B03 记录错误并保留。B02 根据同一覆盖配置及有限值要求确定统计样本，避免未知或长时间陈旧读数污染基准。

缺测拒绝时，S1–S4 记为跳过；其余文件在完整标准 Episode 上运行 S1–S4。S1 异常帧 / 丢弃、S2 丢弃、S3 待删帧 / 丢弃、S4 待删帧任一命中即拒绝整个 MCAP。保留启动排除、S1 宽限及 S3 自带最短长度规则，不新增最短前缀或重复尾段规则，不改变原数值阈值。当前不检查视频模糊、曝光或遮挡。

## 运行

本项目仍依赖 `SLM_data_curation/qwen-manip-preprocess`。上游接口基于提交 `0e694dbc9706b2a5bac8e550f8cd9de18f5b7473`；本分支当前未取得引擎，真实数值计算及多进程接入需在引擎环境复验。

```bash
python3 -m pip install -e /path/to/SLM_data_curation/qwen-manip-preprocess
python3 -m pip install -e /path/to/stage-b-v4-review-fixes

# 完整四阶段：检测并复制合格文件，不删除源文件。
python3 run.py quality --config configs/jindian_marvin.yaml \
  --repo-root /path/to/SLM_data_curation \
  --input-root /path/to/input-mcaps \
  --output-root /path/to/reports/quality-001

# 仅调试前两个阶段，输出统计纳入情况。
python3 run.py stats --config configs/jindian_marvin.yaml \
  --repo-root /path/to/SLM_data_curation \
  --input-root /path/to/input-mcaps \
  --output-root /path/to/reports/stats-001

# 完整四阶段：先交付合格文件，再删除被拒绝的源文件。
python3 run.py run --config configs/jindian_marvin.yaml \
  --repo-root /path/to/SLM_data_curation \
  --input-root /path/to/test-copies \
  --output-root /path/to/reports/run-001
```

三个命令都需要新的输出目录，不能将已执行 `stats` 的目录继续作为 `quality` 的输出。可重复传入 `--mcap` 限定批次；统计只用本批满足纳入要求的文件。`--recompute-stats` 强制重算；缓存指纹包含标准中间数据及配置。单条文件可组成批次，但统计更容易受自身影响。

ABC 使用 `configs/abc_130k.yaml`，同时指定实际 `--repo-root`、`--package-dir`、`--quality-yaml`；外部适配器完成时间轴接口后再运行。它当前关闭夹爪覆盖检查。其他 MCAP topic 布局需要对应适配器及配置。

## 输出与异常

| 输出 | 内容 |
|---|---|
| `normalized/manifest.json` | B01 完整批次清单、输入错误、源身份与标准数据引用 |
| `normalized/episode_*.npz` | 状态、动作、真实质量帧时间轴及有效测量时间轴；没有质检掩码或最终判定 |
| `statistics_context.json` | B02 状态、每条统计纳入 / 排除原因；不是可直接反序列化的引擎统计对象 |
| `cache/` | 数值统计缓存；ABC 可能含适配器源视频缓存 |
| `decisions.jsonl` | B03 每条保留 / 拒绝 / 错误、原因及证据区间 |
| `quality_summary.json` / `quality_batch.json` | B03 汇总及带报告哈希的 B04 输入清单 |
| `accepted/` | B04 交付的合格原始 MCAP，保留相对目录结构 |
| `delivery_plan.json` / `delivery_manifest.json` | 复制计划、完成状态、源与目标对应关系、文件大小及 SHA256 |
| `deletion_plan.json` | B04 拒绝记录和删除资格，在删除前持久保存 |
| `deletion_events.jsonl` | `run` 的实际删除或跳过结果 |
| `run_summary.json` | B04 最终汇总：质量结果、实际导出数、删除数及执行错误 |

B01 中间文件写入失败会停止流程。B02 统计失败时，B03 可记录不依赖统计的缺测拒绝，其余待数值判断文件记为错误；整批禁止删除。`empty` 与 `failed` 不同：全批缺测时没有可拟合数据，仍能在 B03 完成明确的拒绝判定。

合格文件先复制到暂存目录，整批复制完成才发布 `accepted/`。复制过程中出现异常时不发布部分批次，整批源删除禁用；报告写入失败时也不会继续删除。已有交付目录或审计不会覆盖或自动续跑。

输入或判定错误保留源文件，不能等同质量不合格。删除前核对源身份，符号链接相关路径或已变化文件会跳过并审计。只删除源 MCAP，不删除 sidecar 或父目录。时间区间仅是诊断证据，拒绝仍作用于整条文件。存在错误、删除跳过或统计失败时，程序以非零状态结束。

前序质检已拒绝、后序诊断报错时，该条仍为 `rejected`，额外保存 `diagnostic_errors`，禁止删除并返回非零状态。尚无拒绝证据的报错条记为 `error`。`kept_files + rejected_files + error_files = episodes`；`failed_episodes` 统计有输入或诊断执行错误的条数，可与 `rejected_files` 重叠，不能再用前三者加上它求总数。

删除后目录同步失败会立即停止剩余删除，保留已经成功解除文件链接的实际数量；审计关闭失败也不会丢失已完成结果。`deleted_files` 表示本次调用已确认的解除链接数，不保证未同步目录在断电后的持久性。结果不确定的删除单列为 `deletion_unknown_files`，不能当作已删除或未删除。`execution_status` 表示整个运行结果；`delivery_status` 只表示合格副本交付，两者不能混用。主异常及关闭阶段的次级异常分别保存在 `audit_error` 和 `deletion_cleanup_errors`。

B01 标准批次和 B03 判定均可持久化重载，B04 可单独读取判定文件执行。B02→B03 的引擎统计对象目前仍在进程内传递；跨进程平台注册仍需接入该数据端口；本仓库的 YAML 是注册清单，不代表已经部署到平台。

## 验证

本轮完整回归：**137 passed，15 skipped**；另有 768 种合法 MCAP 写法的独立兼容检查全部通过。新增边界样本见 [复查修复交接](docs/REVIEW_FIX_HANDOFF.md)。使用真实编码的合成 MCAP/CDR 验证完整性、解码、时间轴、中间存储、阶段顺序、批次完整性和删除保护；缺失的上游数值统计 / S1–S4 使用明确的测试替身。15 项真实引擎及 ABC 集成仍需在同事环境复验。
