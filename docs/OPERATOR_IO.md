# Stage B v4：算子注册与输入输出字段

本版本只有三个注册 ID：`mcap_read`（MCAP读取）、`global_stats`（全局统计）、`quality_detection`（质量检测）。S1–S4 与夹爪缺测为质量检测内部步骤，不是单独注册的算子。删除源 MCAP 是质量检测的执行阶段，不增加第四个导出算子。

## 输入支持范围

| Profile | 输入布局 / 必需资源 | 质量数组 |
|---|---|---|
| `jindian_marvin` | 根目录或下一层目录中的 `.mcap`；ROS2 CDR 的 Marvin 状态、目标关节和夹爪 topic | `state/action: float64[T,16]`，约 25 Hz；左右各 7 关节及左右夹爪宽度 |
| `abc_130k` | 官方 MCAP 布局与每条旁边的 `eef_pose.parquet` | `state/action: float64[T,32]`，约 30 Hz；关节、夹爪及 EEF |

`T` 是单条 Episode 的质量帧数，`D` 是 profile 定义的数值维度。输入不是任意 MCAP 通用解析；原始 UMI、Ego 和其他机器人需要适配器及配套质量配置。当前没有视频像素质量检查。

## B01：MCAP读取 / `mcap_read`

实现：`stage_b_mcap_v4.operators.McapReadOperator`。调用边界：

```text
run(ref, spec, cfg, adapter) → QualityEpisode
```

| 输入字段 | 类型 | 说明 |
|---|---|---|
| `ref` | `McapEpisodeRef` | Episode 标识、数据根目录、源 MCAP 路径及数据包相对路径 |
| `spec` | `McapSpec` | 数据包、质量配置与输出缓存位置、参考 topic 等运行配置 |
| `cfg` | 上游 `PipelineConfig` | 由 YAML 与覆盖值生成的 Stage B 配置 |
| `adapter` | 数据集适配器对象 | 将指定数据布局转换为统一质量数组 |

| `QualityEpisode` 输出字段 | 类型 | 说明 |
|---|---|---|
| `ref` | `McapEpisodeRef` | 源文件引用，保留词法路径以供符号链接检查 |
| `state` | `float64[T,Ds]` | 实际观测状态；目前两个 profile 的 `Ds` 分别为 16、32 |
| `action` | `float64[T,Da]` | 对齐的目标动作；目前 `Da=Ds` |
| `frame_times_ns` | `int64[T]` | 不倒退的原始纳秒时间戳 |
| `startup_exclude` | 布尔数组 | 启动零值等规则产生的逐通道排除掩码 |
| `stage1_exclude` | 布尔数组 | S1 使用的启动及宽限期排除掩码 |
| `source_identity` | `SourceIdentity` | 源文件设备号、inode、大小、纳秒修改时间，用于删除前核对 |

读取前后检查源文件身份。读取或对齐失败记为输入错误，不能直接作为质量不合格自动删除。

对于简智夹爪整侧缺失的读数，数组中的对应夹爪值为 `NaN`（未知）。质量检测的覆盖预检在全局统计之前拒绝该文件；没有伪造有效零值。关闭覆盖检测仍含未知数值时，按判定错误保留文件，不把它当作通过。

## B02：全局统计 / `global_stats`

实现：`stage_b_mcap_v4.operators.GlobalStatsOperator`。调用边界：

```text
run(spec, cfg, refs, *, recompute=False) → StatisticsBundle
```

CLI 的 `--recompute-stats` 控制是否强制重算。

| 输入字段 | 类型 | 说明 |
|---|---|---|
| `spec`、`cfg` | 配置对象 | 统计方法、阈值倍率、分箱数、路径等 |
| `refs` | `list[McapEpisodeRef]` | 批次文件引用；`quality` / `run` 传入可读取且通过夹爪覆盖预检的子集；允许只有 1 条 |
| `recompute` | 布尔值 | 是否强制重算缓存 |

| `StatisticsBundle` 输出字段 | 类型 | 说明 |
|---|---|---|
| `stage1_stats` | 上游统计对象 | 各通道残差、加速度、jerk 的阈值等 |
| `global_stats` | 上游统计对象 | 状态和动作分位数、极值、帧数和 Episode 数 |
| `alignment_stats` | 上游统计对象 | 状态与动作时滞统计 |
| `alignment_lag` | 整数 | 统计得到的代表性时滞帧数 |
| `cache_dir` | `Path` | 统计缓存目录 |

当前算子内部会读取 `refs`，不是直接聚合一组 B01 输出对象；因此可能与 B01 重复读取源文件。不同统计批次的判定可能不同。`quality` / `run` 先进行读取与夹爪覆盖预检，输入错误和缺测拒绝文件不参与统计；若没有待数值检测的文件，则不调用全局统计。

单独运行 `stats` 时直接使用选中引用，不做覆盖预检，也不写逐条保留 / 拒绝记录。非有限数组会被统计钩子拒绝，再由上游统计逻辑跳过或报错。质量命令中本批需要的全局统计失败时，依赖其结果的文件记为错误且整批删除禁用；预检已拒绝的文件仍记录拒绝，但不会在本次失败运行中删除。全批均为缺测拒绝、无需统计的路径可正常完成删除。

## B03：质量检测 / `quality_detection`

实现：`stage_b_mcap_v4.operators.QualityDetectionOperator`。调用边界：

```text
run(episode, spec, cfg, statistics) → QualityDecision
```

| 输入字段 | 类型 | 说明 |
|---|---|---|
| `episode` | `QualityEpisode` | B01 的单条对齐数据及源文件身份 |
| `spec` | `McapSpec` | 包含可选夹爪缺测规则 |
| `cfg` | `PipelineConfig` | S1–S4 的阈值、排除掩码及相关配置 |
| `statistics` | `StatisticsBundle` | B02 的本批统计基准 |

检测仅包含夹爪缺测和原 S1–S4 的检查。夹爪缺测命中时立即拒绝整条，不再对缺口两侧分段挽救，S1–S4 记录为跳过。没有缺口时，S1–S4 均在完整原 Episode 上执行，不使用有效前缀截断。S1 的异常帧或丢弃、S2 的丢弃、S3 的待删帧或丢弃、S4 的待删帧中任一命中，令 `reject_file=true`。S3 自带的最短长度检查仍然有效；没有独立的最终最短前缀或重复尾段规则。

CLI 先调用同一质量检测类的 `precheck(episode, spec) → QualityDecision | None`，以便在拟合统计前排除夹爪缺测文件。返回判定时已完成整条拒绝；返回 `None` 表示继续拟合统计与数值检测。`run()` 本身也做覆盖检查，直接调用该类仍遵守相同规则。整批都缺测时，无需全局统计即可记录全部拒绝并在执行模式下删除。

| `QualityDecision` 输出字段 | 类型 | 说明 |
|---|---|---|
| `ref` | `McapEpisodeRef` | 源文件引用 |
| `frame_times_ns` | `int64[T]` | 检测帧的原始时间戳 |
| `bad_mask` | `bool[T]` | 触发质量问题的证据帧；不是用于切分导出的保留掩码 |
| `reject_file` | 布尔值 | 是否拒绝整个源 MCAP；实际执行 `run` 时据此删除 |
| `reasons` | 字符串序列 | 触发拒绝的原因 |
| `stage_results` | 字典 | 各内部检测结果、命中计数和指标 |
| `source_identity` | `SourceIdentity` 或空值 | 检测对应的源文件身份；删除还需 CLI 的无符号链接身份检查 |

`reject_file` 是最终整条文件判定，不应只从 `bad_mask` 推断。有些阶段直接给出整段拒绝，未必产生对应逐帧坏点。

## 可持久化判定字段

`QualityDecision.to_record()` 将内存对象变成 JSON 可序列化记录。平台注册为文件型输出时可使用判定 JSONL；数组不必直接暴露给前端配置面板。

| JSON 字段 | 类型 | 含义 |
|---|---|---|
| `episode_index` | 整数 | 批次内 Episode 编号 |
| `source` | 字符串 | 源 MCAP 路径 |
| `status` | `kept` / `rejected` | 质量判定结果；不是删除成功状态 |
| `reject_file` | 布尔值 | 整文件拒绝标志 |
| `reasons` | 字符串数组 | 拒绝原因 |
| `num_frames` | 整数 | 质量帧数 |
| `detected_bad_frames` | 整数 | 证据坏帧数量 |
| `detected_ranges` | 对象数组 | 带规则来源和帧 / 时间边界的证据区间 |
| `effective_removed_ranges` | 对象数组 | 整条拒绝时覆盖全部质量帧时间轴；通过时为空，实际删除作用于整个 MCAP |
| `stage_results` | 对象 | 内部阶段的结果与指标 |
| `source_identity` | 对象 | `st_dev`、`st_ino`、`st_size`、`st_mtime_ns` |

输入失败另写 `status=error` 的记录，并保留源文件。CLI 还会为记录补充 `deletion_eligible`（是否满足删除前置条件）、`deletion_blocked_reason`（阻止删除的原因）。报告中的 `rejected` 表示质量不通过；删除是否成功另由执行记录表示。

## 批次执行与删除

| 命令 | 行为 |
|---|---|
| `stats` | 直接对所选文件计算或读取统计缓存；不做覆盖预检、不删除 |
| `quality` | 读取与夹爪覆盖预检 → 对剩余文件拟合统计 → S1–S4 → 写报告；不删除 |
| `run` | 同上完成检测并持久保存整批判定及删除计划，再删除拒绝的源 MCAP 并记录结果 |

质量检测类提供 `delete_rejected(plan_records, *, input_root, events_path)` 作为执行方法，内部删除辅助函数不注册成第四个算子。删除只针对源 `.mcap`，不删除 sidecar 或父目录。符号链接或父路径含符号链接的文件仍可读取判定，但删除被跳过并审计；源文件在判定后发生变化或输入读取失败的情况保留并记录原因。跳过某条文件不等于整批拒绝执行。

`quality` 和 `run` 都使用内存判定对象；没有通过 v3 的 `keep_mask` 导出新 MCAP 的流程。源文件通过时保持完整，拒绝时在 `run` 中整条删除。报告和删除计划需要先于真实删除持久化，以便回查已删除文件的依据。

CLI 文件输出是 `decisions.jsonl`、`deletion_plan.json`、`run_summary.json`，以及 `run` 实际执行时的 `deletion_events.jsonl`。再次检测必须使用没有旧审计报告的新输出目录；仅含统计缓存的目录可继续使用。

## 节点配置面板支持

常用阈值可使用整数、小数、开关、单选和预设多选；profile 自动提供关节索引、维度和 topic 默认值。输入 MCAP、统计对象和判定属于数据端口，不属于用户手填参数。逐关节上下限、有顺序的 topic 列表仍需要结构化配置；S1–S3 的可选跳过尚未实现，不应在前端展示无效开关。
