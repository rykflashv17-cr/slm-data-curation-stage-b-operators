# Stage B v4：四个算子严格串行接口

四个注册 ID 为 `mcap_read`、`global_stats`、`quality_detection`、`mcap_delivery`。完整检测只调用以下批次接口；每次调用完成后才能执行下一行：

```python
batch = McapReadOperator().run_batch(refs, spec, cfg)
context = GlobalStatsOperator().run(batch, spec, cfg, recompute=False)
judged = QualityDetectionOperator().run_batch(context, spec, cfg)
result = McapDeliveryOperator().run(judged, mode="quality")
```

`mode="run"` 会在第四阶段完成合格文件交付和审计后删除拒绝源文件。S1–S4、夹爪缺测与判定报告属于 B03；复制、交付报告和删除属于 B04。文件发现与配置加载是调度设施，不是额外业务算子。

## B01：格式统一

`run_batch(refs, spec, cfg, adapter=None) → NormalizedBatchRef`。输入为完整的 `McapEpisodeRef` 列表、运行配置及 schema；未提供适配器时由 B01 构建。逐条先做 MCAP 完整结构与可用 CRC 校验，再执行适配器解码、转换、对齐和时间轴校验，然后写入 NPZ；不计算缺测阈值、启动零值掩码、S1 宽限期或通过 / 拒绝。

完整性校验顺序扫描所有记录，包括没有索引的文件、数值 topic 以外的压缩块、附件及摘要。压缩后与声明大小不符、截断或非零 CRC 不符均按输入错误处理。合法省略 CRC 的文件允许通过，不能据此证明无校验和的内容未改变。该步骤增加文件读取及解压开销；适配器的数值解码仍只有一次，不检查视频像素。

| 标准单条 `NormalizedEpisode` 字段 | 类型 / 含义 |
|---|---|
| `ref` | Episode 编号、数据根目录、源路径及包内相对路径 |
| `state` / `action` | `float64[T,Ds]` / `float64[T,Da]`；Jindian 为 16 维，ABC 为 32 维 |
| `frame_times_ns` | `int64[T]`，与最终质量行逐一对应的非负、不倒退源时刻 |
| `valid_measurement_times_ns` | `dict[str,int64[N_topic]]`，每个 topic 的有效测量时刻；不必与 T 相同 |
| `source_identity` | 文件设备号、inode、大小、纳秒修改时间 |

适配器必须提供 `to_quality_timeline(standard)`，与 `to_quality_arrays(standard)` 使用相同最终行序。空整数数组代表没有有效测量，缺少启用检测所需 topic 的键代表接口不完整。B01 保留这些事实，B02/B03 校验所需字段。旧 `reference_topics` 配置保留兼容，不再用于猜测时间轴。

Jindian 整侧缺测时对应值为 NaN，B01 不把它补成有效零值。读取 / 对齐失败写入输入错误；中间存储失败则使 B01 整体失败，不启动 B02。

`NormalizedBatchRef` 含 `manifest_path` 和完整 `records` 元组。每个 `NormalizedRecord` 包含 `ref`、`data_path`、`source_identity`、`input_error`、`deletion_blocked_reason`。输入错误记录仍在清单中，`data_path=None`。后一字段表示源路径是否满足删除设施要求，不是质量判定。

`normalized/manifest.json` 引用同目录 NPZ；可用 `load_batch(path)` 重载批次、`load_episode(record)` 读取单条。NPZ 只包含数值数组、时间轴和 topic 字符串，不使用 pickle。B01 的单条 `run(ref,spec,cfg,adapter)` 保留为格式统一辅助接口；完整平台串行应使用 `run_batch`。

## B02：全局统计

`run(batch, spec, cfg, *, recompute=False) → StatisticsContext`。只消费 B01 标准产物；不构建源适配器、不重新解码 MCAP、不调用 B03。

统计纳入要求：B01 成功、配置要求的测量覆盖充分、状态和动作均有限。B02 使用与 B03 相同的覆盖配置确定统计样本，只记录 `fit_status=included/excluded` 与原因；不生成最终 `rejected/kept`、质量坏帧掩码或删除计划。

| `StatisticsContext` 字段 | 含义 |
|---|---|
| `batch` | 原完整 `NormalizedBatchRef`，不是只包含纳入样本的子集 |
| `status` | `ready`：拟合成功；`empty`：没有候选；`failed`：中间数据损坏或引擎拟合失败 |
| `statistics` | `ready` 时为 `StatisticsBundle`，其他状态为 None |
| `fit_records` | 每条是否纳入及原因，仅为统计人口记录 |
| `coverage_policy` | 本次统计使用的左右 topic 与缺测阈值；B03 校验不能中途改变 |
| `error` | 统计失败详情 |

`StatisticsBundle` 包含 `global_stats`（S3 分位数等）、`stage1_stats`（S1 阈值等）、`alignment_stats`、`alignment_lag`、`cache_dir`。计算仍委托原上游引擎，通过 `_NormalizedQualityHook` 读取 NPZ。候选为零时 B02 仍完整执行并写出 `empty`，但不调用数值拟合。引擎返回零统计帧按失败处理。

缓存指纹绑定标准批次、NPZ 内容与配置。`statistics_context.json` 是审计摘要，数值缓存位于 `cache/`；它不是带有全部引擎对象的可重载 `StatisticsContext`。B02→B03 在同一 Python 进程内串接；跨进程平台需要另行对接统计对象的传输或加载。B03→B04 已有独立的文件交接接口。

## B03：质检判断

`run_batch(context, spec, cfg, *, started=None) → BatchQualityResult`。完整批次只能从 B02 的 context 进入。B03 遍历所有记录，输入错误照常报告，其余读取标准数据并调用单条 `run(episode,spec,cfg,statistics)`。

1. 检查有效测量覆盖；任一侧到最近有效读数的距离严格大于阈值即拒绝整条，S1–S4 跳过。距离等于阈值不命中。
2. 没有已确诊缺测却存在非有限值，或需要数值判断但统计不可用，记为错误并保留。
3. 在 B03 内生成启动 / S1 宽限掩码，执行原 S1–S4，合并整条判断。
4. 保存 `decisions.jsonl`、`quality_summary.json` 及带二者 SHA256 的 `quality_batch.json`，结束第三阶段。B03 不复制、删除源文件，也不生成删除计划。

B03 保留完整数值诊断，S1–S4 内尚未改成逐项失败短路。`precheck` 只是第三阶段的内部覆盖方法，不是 CLI 或 B02 的前置入口。S1–S4 使用完整标准 Episode，不先截断前缀。S1、S2、S3 的整段丢弃也会拒绝文件，即使没有逐帧坏点。`QualityDecision.reject_file` 才是整条结果，不能仅从 `bad_mask` 推断。

每个成功完成的数值阶段先保存判定，再执行下一阶段。执行报错时停止余下阶段、保存 `diagnostic_errors`；已获得的拒绝及区间仍保留为 `rejected`，否则为 `error`。二者均禁止该条源文件删除，返回非零状态。质量规则命中本身不触发这种执行异常短路。

| B03 结果字段 | 含义 |
|---|---|
| `BatchQualityResult.summary` / `exit_code` | 质检汇总及是否有判定错误；不代表文件已交付 |
| `BatchQualityResult.manifest_path` | `quality_batch.json`，作为 B04 可重载输入 |
| `status` | 逐条报告中的 `kept` / `rejected` / `error` |
| `reject_file` / `reasons` | 整条拒绝及其原因 |
| `num_frames` / `detected_bad_frames` | 质量帧数与证据坏帧数 |
| `detected_ranges` / `stage_results` | 各阶段诊断区间、指标与状态 |
| `diagnostic_errors` | 阶段名到执行异常文本的映射；非空时禁止源删除 |
| `effective_removed_ranges` | 拒绝时覆盖完整质量时间轴，仅为诊断；实际删除整个 MCAP |
| `source_identity` | 删除前核对的源身份 |
| `deletion_eligible` / `deletion_blocked_reason` | 删除设施资格及阻止原因，不等于删除已成功 |

若 B02 `failed`，全部源删除禁用；可继续写明确的缺测拒绝，其余需要统计的文件记为错误。若 B02 `empty`，明确的缺测拒绝可正常完成；无法确定数值质量的文件仍为错误。输入错误始终保留。删除变化文件、符号链接路径、缺少身份的文件会跳过并审计。

汇总三分类为 `kept_files`、`rejected_files`、`error_files`，总和等于 `episodes`。`diagnostic_error_files` 统计含阶段执行异常的条数，`failed_episodes` 统计 `error` 与有诊断异常记录的并集，因此可以与 `rejected_files` 重叠。新交接文件为 `quality_batch_v2`；旧的无诊断异常 `quality_batch_v1` 仍可重载，验证后补齐新计数。平台读取汇总时需按新语义处理。

## B04：结果交付

`McapDeliveryOperator.run(batch: BatchQualityResult, *, mode="quality") → BatchDeliveryResult`。B04 只读取 B03 保存的判断及源文件字节，不需要 `cfg`、统计对象或标准 NPZ，不重新质检。

也可以跨进程加载同一文件系统中的 B03 结果：

```python
from stage_b_mcap_v4 import load_quality_batch, McapDeliveryOperator
judged = load_quality_batch("/absolute/reports/quality_batch.json")
result = McapDeliveryOperator().run(judged, mode="quality")
```

重载时校验判定与汇总的 SHA256、条目数量、状态与拒绝标志的一致性及重复源/编号。交接文件中的绝对源路径必须在 B04 环境中可访问；不是可任意换挂载点的可移植数据包。

| 输入状态 | 第四阶段行为 |
|---|---|
| `kept` | 核对源身份后复制到 `accepted/<相对输入根目录路径>`，保持 MCAP 字节原样 |
| `rejected` | 不输出；`quality` 保留原件，`run` 在交付和审计完成后尝试删除；有 `diagnostic_errors` 的条目强制保留 |
| `error` | 不输出也不删除；保留错误记录并返回非零状态 |
| 统计失败 | 不交付，整批禁止删除 |
| 复制失败 | 不发布不完整批次，整批禁止删除 |

交付先写 `delivery_plan.json`，在专用暂存目录完成全部复制、核对身份并持久化，再发布 `accepted/`。`delivery_manifest.json` 的 `complete` 表示所有应交付文件已完成；只有此时 `exported_files` 计入成功文件。若发布后目录同步或审计失败，目录可能存在，但缺少成功清单，不能当作成功批次；不会因此继续删除源文件。清单外的报告不放入 `accepted/`。

源文件在 B03 后或复制中变化、符号链接路径、缺少身份等情况不能复制成已合格文件。复制是独立文件，不使用硬链接。只复制 MCAP，不复制 ABC 的 parquet sidecar；原 sidecar 继续保留。第四阶段不将源 MCAP 改写成标准 topic/schema。

B04 持久保存 `delivery_manifest.json`、`deletion_plan.json` 后，才按模式执行删除。每次解除链接后立即记录实际结果；后续目录同步、审计写入或关闭失败都停止删除，携带已完成结果返回，次级关闭异常不会覆盖主异常。删除调用报错且结果无法确定时记 `unknown`，不声称未删除。

返回 `BatchDeliveryResult(summary, exit_code)`；汇总包含 `execution_status`（整个运行）、`delivery_status`（仅合格副本交付）、`exported_files`、`accepted_root`、`deleted_files`、`deletion_skipped_files`、`deletion_unknown_files`、`deletion_sync_failed_files` 及错误详情。`deleted_files` 包含已确认解除链接但目录同步失败的项，不能代表断电持久性；详情见逐条 `source_directory_synced`。`audit_error` 保存删除阶段主异常，`deletion_cleanup_errors` 保存次级关闭异常。上述执行失败均使退出码为非零。

所有条目拒绝时可以成功输出空的 `accepted/`，再在 `run` 模式删除明确拒绝项；质量或输入错误不会变成“成功通过”。已有输出、计划或审计拒绝覆盖，不能对已经执行过 `quality` 交付的目录再次执行 `run` 隐式删除。

## 调用迁移

B03 的 `mode` 参数和 `delete_rejected` 方法已移除。调用者先取得 B03 结果，再显式调用 B04；执行模式只交给 B04。前三个算子的串联顺序和原有两个 P0 修复保留。

更早的 `GlobalStatsOperator.run(spec,cfg,refs)` 和带启动掩码的 `QualityEpisode` 构造方式仍不适用；`QualityEpisode` 仅是 `NormalizedEpisode` 的导入别名。

CLI 的 `stats` 仍执行 1→2；`quality/run` 均执行完整 1→2→3→4。`quality` 现在也输出合格 MCAP 副本，但保留全部输入。所有命令使用新的输出目录。最终查看 `run_summary.json` 和交付清单，不能只凭 `kept_files` 判断复制成功。
